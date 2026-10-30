"""Isolated SQL recovery tests; no provider, production database or paid calls."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from app import wait_recovery
from app.budget import cancel_undispatched, reserve
from app.budget_admin import BudgetChange, change_budget
from app.chat import Submission, request_view
from app.database import Base, transaction
from app.intent_router import submit_auto
from app.member_experience import activity_view
from app.member_status import public_event, waiting_status
from app.models import (
    BudgetAccount,
    CallLimit,
    ChatSession,
    IdempotencyRecord,
    Member,
    MemberGroup,
    ModelCall,
    ProfileSnapshot,
    Request,
    RequestEvent,
    User,
    WorkItem,
    now,
)
from app.work_queue import claim_next, wait_for


class Capacity:
    active = 0
    counts = {}

    def time(self):
        return 1800000000, 0

    def register_script(self, _):
        return None

    def scard(self, _):
        return self.active

    def zcount(self, key, *_):
        return self.counts.get(key, 0)

    def close(self):
        pass


@pytest.fixture
def studio(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr("app.database.engine", lambda: engine)
    settings = SimpleNamespace(
        model_calls_enabled=True,
        default_model="gpt-6-luna",
        model_concurrency_limit=1,
        redis_url="redis://unused",
        credentials=lambda _: SecretStr("synthetic"),
    )
    monkeypatch.setattr(wait_recovery, "get_settings", lambda: settings)
    monkeypatch.setattr("app.call_limits.get_settings", lambda: settings)
    capacity = Capacity()
    capacity.counts = {}
    monkeypatch.setattr(wait_recovery.Redis, "from_url", lambda *a, **k: capacity)
    with transaction() as db:
        owner = User(email="owner@test", password_hash="unused", display_name="Owner", role="Owner")
        user = User(email="member@test", password_hash="unused", display_name="Member")
        group = MemberGroup(
            name="Default", configuration={"capabilities": {"training": True, "nutrition": True}}
        )
        db.add_all([owner, user, group])
        db.flush()
        member = Member(user_id=user.id, group_id=group.id)
        session = ChatSession(user_id=user.id)
        db.add_all([member, session])
        db.flush()
        db.add(ProfileSnapshot(member_id=member.id, version=1, facts={}, sources={}, confirmed_by=user.id))
        request = Request(
            user_id=user.id,
            session_id=session.id,
            kind="Auto",
            status="Waiting",
            deadline=now() + timedelta(hours=1),
            payload={
                "content": "Create my plans",
                "auto": True,
                "role": "Intake",
                "recovery_prerequisites": [],
            },
        )
        db.add(request)
        db.flush()
        item = WorkItem(
            request_id=request.id,
            role="Intake",
            subject_kind="Conversation",
            subject_id=session.id,
            status="Waiting",
            wait_reason="BudgetRequired",
            attempts=0,
            wait_context={"model": "gpt-6-luna", "provider": "openai", "amount_micros": 30000},
        )
        db.add(item)
        scopes = ["global", f"group:{group.id}", f"member:{member.id}", "role:Intake"]
        for scope in scopes:
            db.add(
                BudgetAccount(
                    scope_key=scope, limit_micros=1000000, spent_micros=18000, reserved_micros=956826
                )
            )
        for scope, calls in [
            ("initial", 200),
            ("model:openai:gpt-6-luna", 100),
            (f"group:{group.id}", 200),
            (f"member:{member.id}", None),
        ]:
            db.add(CallLimit(scope_key=scope, calls=calls))
        db.flush()
        ids = SimpleNamespace(
            request=request.id,
            work=item.id,
            member=member.id,
            user=user.id,
            owner=owner.id,
            group=group.id,
            session=session.id,
            scopes=scopes,
            capacity=capacity,
        )
    yield ids
    engine.dispose()


def fund(scopes):
    with transaction() as db:
        for row in db.scalars(select(BudgetAccount).where(BudgetAccount.scope_key.in_(scopes))):
            row.limit_micros = 40000000


def test_all_scopes_must_be_available_then_original_task_resumes_once(studio):
    assert wait_recovery.resume_waiting(force=True) == 0
    fund(["global"])
    assert wait_recovery.resume_waiting(force=True) == 0
    fund(studio.scopes)
    assert wait_recovery.resume_waiting(force=True) == 1
    assert wait_recovery.resume_waiting(force=True) == 0
    with transaction() as db:
        item, request = db.get(WorkItem, studio.work), db.get(Request, studio.request)
        assert item.status == request.status == "Queued"
        assert request.model_calls == 0 and request.payload["content"] == "Create my plans"
        assert db.scalar(select(func.count()).select_from(WorkItem)) == 1
        assert db.scalar(select(func.count()).select_from(RequestEvent)) == 1
        assert all(
            a.reserved_micros == 956826 and a.spent_micros == 18000 for a in db.scalars(select(BudgetAccount))
        )


def test_web_budget_save_triggers_recovery_after_commit(studio):
    # All other scopes are ready, leaving exactly the global budget to edit.
    fund(studio.scopes[1:])
    with transaction() as db:
        row = db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == "global"))
        from app.budget_admin import budget_view

        basis = budget_view(row)["fingerprint"]
        owner = db.get(User, studio.owner)
    change_budget(
        BudgetChange(
            scope_key="global", limit_micros=40000000, expected_fingerprint=basis, reason="Restore service"
        ),
        owner,
    )
    with transaction() as db:
        assert db.get(Request, studio.request).status == "Queued"


@pytest.mark.parametrize("call_status", ["UsageUnknown", "Dispatching", "Reserved"])
def test_uncertain_or_inflight_call_never_repeated(studio, call_status):
    fund(studio.scopes)
    with transaction() as db:
        db.add(
            ModelCall(
                request_id=studio.request,
                user_id=studio.user,
                role="Intake",
                model="gpt-6-luna",
                status=call_status,
                reserved_micros=400,
                price_version="test",
            )
        )
    assert wait_recovery.resume_waiting(force=True) == 0


@pytest.mark.parametrize("status", ["Cancelled", "Stopped", "Completed", "AwaitingConfirmation"])
def test_ended_or_confirmation_request_not_revived(studio, status):
    fund(studio.scopes)
    with transaction() as db:
        db.get(Request, studio.request).status = status
    assert wait_recovery.resume_waiting(force=True) == 0


def test_expired_policy_changed_and_backoff_waits_stay_waiting(studio):
    fund(studio.scopes)
    with transaction() as db:
        db.get(WorkItem, studio.work).resume_after = now() + timedelta(minutes=5)
    assert wait_recovery.resume_waiting() == 0
    with transaction() as db:
        db.get(WorkItem, studio.work).input_versions = {"profile": 999}
    assert wait_recovery.resume_waiting(force=True) == 0
    with transaction() as db:
        db.get(WorkItem, studio.work).input_versions = {}
        db.get(Request, studio.request).deadline = now() - timedelta(seconds=1)
    assert wait_recovery.resume_waiting(force=True) == 0


@pytest.mark.parametrize("scope", ["hour", "day", "concurrency"])
def test_rate_capacity_preflight_does_not_consume_calls(studio, scope):
    from app.rate_limit import ModelRateLimiter

    fund(studio.scopes)
    gate = ModelRateLimiter(studio.capacity)
    if scope == "hour":
        studio.capacity.counts[gate.model_key("openai", "gpt-6-luna")] = 100
    elif scope == "day":
        studio.capacity.counts[gate.account_key(studio.user)] = 200
    else:
        studio.capacity.active = 1
    assert wait_recovery.resume_waiting(force=True) == 0
    studio.capacity.counts.clear()
    studio.capacity.active = 0
    assert wait_recovery.resume_waiting(force=True) == 1


def test_no_dispatch_capacity_wait_does_not_exhaust_attempts_or_call_bound(studio):
    fund(studio.scopes)
    wait_recovery.resume_waiting(force=True)
    for _ in range(5):
        claim = claim_next(request_id=studio.request)
        assert claim is not None
        call = reserve(studio.request, "Intake", "gpt-6-luna", 500, "test", studio.scopes)
        cancel_undispatched(call)
        cancel_undispatched(call)
        wait_for(claim, "HourlyCallLimit", {"model": "gpt-6-luna", "amount_micros": 500})
        assert wait_recovery.resume_waiting(force=True) == 1
    with transaction() as db:
        assert db.get(Request, studio.request).model_calls == 0
        item = db.get(WorkItem, studio.work)
        assert item.attempts == item.capacity_waits == 5


def test_member_snapshot_and_events_hide_internal_evidence(studio):
    with transaction() as db:
        request = db.get(Request, studio.request)
        request.result = {"budget_micros": 4000, "provider_error": "secret", "needs_input": False}
        result = request_view(db, request)
        experience = activity_view(db, request)
    assert experience["status"] == "Temporarily delayed"
    assert experience["no_action_needed"] and experience["auto_resume"]
    assert "budget" not in str(result).lower() and "secret" not in str(result)
    public = public_event({"status": "Waiting", "reason": "BudgetRequired", "reserved_micros": 4000})
    assert "BudgetRequired" not in str(public) and "4000" not in str(public)
    assert waiting_status("UsageReconciliation")[0] == "Checking your request"


def test_duplicate_submit_reuses_waiting_task_and_keeps_other_queries_available(studio):
    with transaction() as db:
        user = db.get(User, studio.user)
        session = db.get(ChatSession, studio.session)
        body = Submission(content="Create my plans", key="duplicate-test", purpose="Auto")
        result = submit_auto(db, session, body, user)
        assert result["request_id"] == studio.request and result["already_pending"]
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Request)) == 1
        assert db.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
        body = Submission(content="Show my plans", key="lookup-test", purpose="Auto", action="ViewPlans")
        result = submit_auto(db, db.get(ChatSession, studio.session), body, db.get(User, studio.user))
        assert result["request_id"] != studio.request
        assert result["status"] == "Completed"


def test_member_http_history_and_sse_remain_public(studio):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.security import principal

    with transaction() as db:
        user = db.get(User, studio.user)
        db.add(
            RequestEvent(
                request_id=studio.request,
                event_type="waiting",
                payload={
                    "status": "Waiting",
                    "reason": "BudgetRequired",
                    "reserved_micros": 956826,
                    "provider_error": "PRIVATE_DETAIL",
                    "role": "Intake",
                },
            )
        )
    app.dependency_overrides[principal] = lambda: user
    try:
        with TestClient(app) as client:
            history = client.get(f"/api/conversations/{studio.session}")
            assert history.status_code == 200
            assert history.json()["requests"][0]["experience"]["status"] == "Temporarily delayed"
            stream = client.get(f"/api/requests/{studio.request}/events")
            assert stream.status_code == 200
            assert "BudgetRequired" not in stream.text
            assert "PRIVATE_DETAIL" not in stream.text and "956826" not in stream.text
            assert "automatically" in stream.text
            assert client.get("/api/owner/budgets").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_shared_quota_service_change_wakes_original_task(studio):
    from app.call_limits import LimitChange, change, entry
    from app.rate_limit import ModelRateLimiter

    fund(studio.scopes)
    scope = "model:openai:gpt-6-luna"
    studio.capacity.counts[ModelRateLimiter(studio.capacity).model_key("openai", "gpt-6-luna")] = 100
    assert wait_recovery.resume_waiting(force=True) == 0
    with transaction() as db:
        before = entry(db, scope)
        change(
            db,
            LimitChange(
                scope_key=scope,
                calls=500,
                expected_fingerprint=before["fingerprint"],
                reason="Restore service capacity",
            ),
            db.get(User, studio.owner),
        )
    with transaction() as db:
        assert db.get(Request, studio.request).status == "Queued"


def test_completed_steps_are_not_reset_and_disabled_service_waits(studio, monkeypatch):
    fund(studio.scopes)
    settings = wait_recovery.get_settings()
    settings.model_calls_enabled = False
    with transaction() as db:
        completed = WorkItem(
            request_id=studio.request,
            role="Coach",
            subject_kind="Conversation",
            subject_id=studio.session,
            status="Completed",
            fence=7,
        )
        db.add(completed)
        db.flush()
        completed_id = completed.id
    assert wait_recovery.resume_waiting(force=True) == 0
    settings.model_calls_enabled = True
    assert wait_recovery.resume_waiting(force=True) == 1
    with transaction() as db:
        completed = db.get(WorkItem, completed_id)
        assert completed.status == "Completed" and completed.fence == 7
