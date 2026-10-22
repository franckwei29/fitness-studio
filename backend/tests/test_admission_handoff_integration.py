"""Committed admission survives handoff failures and new exclusions block service."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from test_admission_integration import facts, submit
from test_admission_integration import member as member_fixture

from app.activation import plan_fingerprint
from app.database import transaction
from app.models import CurrentPlans, Member, PlanVersion, ProfileSnapshot, Request, WorkItem, now
from app.rules import EXCLUSIONS
from app.worker import run_once

member = member_fixture


def test_failed_task_persistence_preserves_admission_and_retry_is_unique(member):
    accepted = submit(member, facts())
    session = member.post("/api/conversations").json()
    endpoint = f"/api/conversations/{session['id']}/messages"
    body = {"key": str(uuid4()), "purpose": "Training", "content": "Create a training routine."}

    def fail_work_persistence(db, context, instances):
        if any(isinstance(row, WorkItem) for row in db.new):
            raise RuntimeError("Synthetic task persistence failure")

    event.listen(Session, "before_flush", fail_work_persistence)
    try:
        with pytest.raises(RuntimeError, match="Synthetic task persistence failure"):
            member.post(endpoint, json=body)
    finally:
        event.remove(Session, "before_flush", fail_work_persistence)
    profile = member.get("/api/me/profile").json()
    assert profile["member"]["id"] == accepted["member_id"]
    assert profile["member"]["status"] == "Accepted"
    assert profile["snapshot"]["version"] == 1
    history = member.get(f"/api/conversations/{session['id']}").json()
    assert history["requests"] == [] and history["messages"] == []
    retried = member.post(endpoint, json=body)
    assert retried.status_code == 202, retried.text
    assert member.post(endpoint, json=body).json() == retried.json()
    with transaction() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(WorkItem)
                .where(WorkItem.request_id == retried.json()["request_id"])
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProfileSnapshot)
                .where(ProfileSnapshot.member_id == accepted["member_id"])
            )
            == 1
        )
    assert member.post(f"/api/requests/{retried.json()['request_id']}/cancel").status_code == 200


@pytest.mark.parametrize("exclusion", EXCLUSIONS)
def test_confirmed_exclusion_suspends_saved_and_queued_services(member, monkeypatch, exclusion):
    accepted = submit(member, facts())
    member_id = accepted["member_id"]
    with transaction() as db:
        registered = db.get(Member, member_id)
        source = Request(
            user_id=registered.user_id,
            kind="Combined",
            status="Completed",
            deadline=now() + timedelta(hours=1),
        )
        db.add(source)
        db.flush()
        current = db.get(CurrentPlans, member_id)
        for kind in ("Training", "Nutrition"):
            plan = PlanVersion(
                member_id=member_id,
                request_id=source.id,
                kind=kind,
                version=1,
                status="Active",
                profile_version=1,
                config_version=1,
                agent_version_id="synthetic",
                content={"title": f"Historical {kind}"},
                rationale="Synthetic availability fixture",
                fingerprint="pending",
            )
            db.add(plan)
            db.flush()
            plan.fingerprint = plan_fingerprint(plan)
            setattr(current, f"{kind.lower()}_id", plan.id)
        current.version = 1
    session = member.post("/api/conversations").json()
    url = f"/api/conversations/{session['id']}/messages"
    queued = member.post(
        url, json={"key": str(uuid4()), "purpose": "Combined", "content": "Revise my routine."}
    )
    assert queued.status_code == 202
    changed = submit(member, facts(**{exclusion: True}))
    assert changed["status"] == "ReferredOut"
    profile = member.get("/api/me/profile").json()
    assert profile["member"]["status"] == "Suspended"
    history = member.get("/api/me/plans").json()
    assert len(history["plans"]) == 2
    assert all(not plan["availability"]["usable"] for plan in history["plans"])
    assert history["current"]["version"] == 1
    for purpose in ("Training", "Nutrition", "Combined"):
        assert (
            member.post(
                url, json={"key": str(uuid4()), "purpose": purpose, "content": "Create a plan."}
            ).status_code
            == 409
        )

    def forbidden(*args, **kwargs):
        pytest.fail("A suspended member must not reach model inference.")

    monkeypatch.setattr("app.coach_runtime.generate", forbidden)
    monkeypatch.setattr("app.nutrition_runtime.generate", forbidden)
    assert not run_once(queued.json()["request_id"])
    result = member.get(f"/api/requests/{queued.json()['request_id']}").json()
    assert result["status"] == "Waiting" and result["model_calls"] == 0
    with transaction() as db:
        task = db.scalar(select(WorkItem).where(WorkItem.request_id == queued.json()["request_id"]))
        assert task.wait_reason == "PolicyReview" and task.attempts == 0
    assert member.post(f"/api/requests/{queued.json()['request_id']}/cancel").status_code == 200
