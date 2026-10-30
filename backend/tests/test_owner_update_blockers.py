"""Budget alerts group requests without changing money or calling a provider."""

from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import BudgetAccount, Request, User, WorkItem, now
from app.owner_updates import blocking_items


def test_budget_blockers_are_grouped_by_scope_and_do_not_mutate_ledgers(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr("app.policy.execution_context", lambda *_: {"scope_keys": ["global", "role:Intake"]})
    with Session(engine) as db:
        user = User(email="fixture@example.test", display_name="Fixture", password_hash="unused")
        db.add(user)
        db.flush()
        budget = BudgetAccount(scope_key="global", limit_micros=100, spent_micros=20, reserved_micros=70)
        db.add_all([budget, BudgetAccount(scope_key="role:Intake", limit_micros=1000)])
        ids = []
        for _ in range(2):
            request = Request(
                user_id=user.id, kind="Chat", status="Waiting", deadline=now() + timedelta(hours=1)
            )
            db.add(request)
            db.flush()
            ids.append(request.id)
            db.add(
                WorkItem(
                    request_id=request.id,
                    role="Intake",
                    subject_kind="Test",
                    subject_id=user.id,
                    status="Waiting",
                    wait_reason="BudgetRequired",
                    wait_context={"amount_micros": 50},
                )
            )
        db.flush()
        items = blocking_items(db)
        assert len(items) == 1 and items[0]["target"] == {"page": "budgets", "id": "global"}
        assert set(items[0]["affected_requests"]) == set(ids)
        assert items[0]["title"] == "Whole studio"
        assert (budget.spent_micros, budget.reserved_micros) == (20, 70)
        budget.limit_micros = 200
        db.flush()
        assert blocking_items(db) == []
