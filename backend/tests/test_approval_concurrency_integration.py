"""Concurrent owner decisions serialize and survive connection recreation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from test_activation_integration import candidate as candidate_fixture

from app.activation import pair_fingerprint
from app.admission import Decision, decide
from app.database import engine, transaction
from app.models import Approval, AuditEvent, PlanPair, PlanVersion, Request, User, WorkItem, now

candidate = candidate_fixture


@pytest.mark.parametrize("decisions", [("Revise", "Revise"), ("Approved", "Rejected")])
def test_concurrent_plan_decisions_record_one_effect(candidate, decisions):
    with transaction() as db:
        owner = User(
            email=f"reviewer-{uuid4()}@example.test",
            display_name="Reviewer",
            role="Owner",
            password_hash="Unused",
        )
        db.add(owner)
        pair = db.get(PlanPair, candidate["pair_id"])
        digest = pair_fingerprint(
            db.get(PlanVersion, pair.training_id), db.get(PlanVersion, pair.nutrition_id)
        )
        request = db.get(Request, candidate["request_id"])
        request.result = {"pair_id": pair.id}
        request.status = "Completed"
        approval = Approval(
            subject_kind="PlanPair",
            subject_id=pair.id,
            subject_version=1,
            fingerprint=digest,
            scope={"member_id": candidate["member_id"]},
            context={},
            expires_at=now() + timedelta(hours=1),
        )
        db.add(approval)
        db.flush()
        approval_id = approval.id
    barrier = Barrier(2)

    def perform(value):
        barrier.wait(timeout=10)
        try:
            result = decide(
                approval_id, Decision(decision=value, fingerprint=digest, comment="Clarify the plan."), owner
            )
            return 200, result
        except HTTPException as exc:
            return exc.status_code, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(perform, decisions))
    assert sorted(status for status, _ in results) == (
        [200, 200] if decisions[0] == decisions[1] else [200, 409]
    )
    successful = [result for status, result in results if status == 200]
    assert all(result == successful[0] for result in successful)
    # Dispose connections only after both transactions finish; re-read durable records.
    engine().dispose()
    with transaction() as db:
        saved = db.get(Approval, approval_id)
        assert saved.decision == successful[0]["decision"]
        assert saved.decided_by == owner.id
        request = db.get(Request, candidate["request_id"])
        assert request.revisions == (1 if saved.decision == "Revise" else 0)
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.subject_id == candidate["pair_id"],
                    AuditEvent.event_type == "plan_pair.reviewed",
                )
            )
            == 1
        )
        tasks = list(db.scalars(select(WorkItem).where(WorkItem.request_id == request.id)))
        assert len(tasks) == (1 if saved.decision == "Revise" else 0)
        for task in tasks:
            assert task.status == "Queued"
            task.status = "Cancelled"
        request.status = "Cancelled"
