"""Real MySQL row locks fence concurrent automatic recovery of one task."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import func, select

from app.database import transaction
from app.models import BudgetAccount, Request, RequestEvent, User, WorkItem, now
from app.rate_limit import ModelRateLimiter
from app.settings import get_settings
from app.wait_recovery import resume_waiting


def test_concurrent_recovery_queues_original_work_exactly_once(monkeypatch):
    settings = get_settings().model_copy(
        update={"model_calls_enabled": True, "gemini_api_key": SecretStr("synthetic-never-dispatched")}
    )
    namespace = f"studio:recovery-test:{uuid4()}"
    monkeypatch.setattr("app.wait_recovery.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.wait_recovery.ModelRateLimiter", lambda connection: ModelRateLimiter(connection, namespace)
    )
    with transaction() as db:
        global_account = db.scalar(
            select(BudgetAccount).where(BudgetAccount.scope_key == "global").with_for_update()
        )
        previous = global_account.limit_micros
        global_account.limit_micros = global_account.spent_micros + global_account.reserved_micros + 1000000
        role = db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == "role:Intake"))
        previous_role = role.limit_micros if role else None
        if not role:
            role = BudgetAccount(scope_key="role:Intake", limit_micros=0)
            db.add(role)
            db.flush()
        role.limit_micros = role.spent_micros + role.reserved_micros + 1000000
        user = User(
            email=f"recovery-{uuid4()}@example.test", display_name="Recovery test", password_hash="unused"
        )
        db.add(user)
        db.flush()
        request = Request(
            user_id=user.id,
            kind="Chat",
            status="Waiting",
            deadline=now() + timedelta(minutes=10),
            payload={"content": "Keep this request"},
        )
        db.add(request)
        db.flush()
        item = WorkItem(
            request_id=request.id,
            role="Intake",
            subject_kind="Test",
            subject_id=user.id,
            status="Waiting",
            wait_reason="BudgetRequired",
            wait_context={"model": settings.default_model, "amount_micros": 100},
        )
        db.add(item)
        db.flush()
        request_id, work_id = request.id, item.id
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: resume_waiting(force=True), range(8)))
        assert sum(results) >= 1
        with transaction() as db:
            assert db.get(Request, request_id).status == "Queued"
            item = db.get(WorkItem, work_id)
            assert item.status == "Queued" and item.fence == 1
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(RequestEvent)
                    .where(
                        RequestEvent.request_id == request_id, RequestEvent.event_type == "recovery_queued"
                    )
                )
                == 1
            )
            assert (
                db.scalar(select(func.count()).select_from(WorkItem).where(WorkItem.request_id == request_id))
                == 1
            )
    finally:
        with transaction() as db:
            db.scalar(
                select(BudgetAccount).where(BudgetAccount.scope_key == "global").with_for_update()
            ).limit_micros = previous
            role = db.scalar(
                select(BudgetAccount).where(BudgetAccount.scope_key == "role:Intake").with_for_update()
            )
            if previous_role is None:
                db.delete(role)
            else:
                role.limit_micros = previous_role
