"""Real database concurrency and settlement checks without provider requests."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.budget import BudgetDenied, mark_dispatching, mark_unknown, reserve, settle
from app.database import transaction
from app.models import BudgetAccount, ModelCall, Request, User, now


@pytest.fixture
def budget_requests():
    scope = f"test:{uuid4()}"
    with transaction() as db:
        global_account = db.scalar(
            select(BudgetAccount).where(BudgetAccount.scope_key == "global").with_for_update()
        )
        previous_limit = global_account.limit_micros
        global_account.limit_micros = global_account.spent_micros + global_account.reserved_micros + 10000
        db.add(BudgetAccount(scope_key=scope, limit_micros=100))
        user = User(email=f"{uuid4()}@example.test", password_hash="Unused", display_name="Budget Test")
        db.add(user)
        db.flush()
        requests = [
            Request(user_id=user.id, kind="Test", deadline=now() + timedelta(minutes=5)) for _ in range(2)
        ]
        db.add_all(requests)
        db.flush()
        ids = [row.id for row in requests]
    yield scope, ids
    with transaction() as db:
        global_account = db.scalar(
            select(BudgetAccount).where(BudgetAccount.scope_key == "global").with_for_update()
        )
        global_account.limit_micros = previous_limit


def test_concurrent_reservations_and_unknown_usage(budget_requests):
    scope, requests = budget_requests

    def attempt(identifier):
        try:
            return reserve(identifier, "Coach", "test-model", 100, "test-price", [scope])
        except BudgetDenied:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, requests))
    calls = [result for result in results if result]
    assert len(calls) == 1
    call_id = calls[0]
    assert mark_dispatching(call_id)
    assert not mark_dispatching(call_id)
    mark_unknown(call_id)
    with transaction() as db:
        account = db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == scope))
        assert account.reserved_micros == 100
        assert account.spent_micros == 0
        assert db.get(ModelCall, call_id).status == "UsageUnknown"
    settle(call_id, 40, {"output_tokens": 2})
    settle(call_id, 40, {"output_tokens": 2})
    with pytest.raises(ValueError, match="conflicts"):
        settle(call_id, 50, {"output_tokens": 3})
    with transaction() as db:
        account = db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == scope))
        assert account.reserved_micros == 0
        assert account.spent_micros == 40


def test_only_undispatched_reservations_can_be_cancelled(budget_requests):
    from app.budget import cancel_undispatched

    scope, requests = budget_requests
    call_id = reserve(requests[0], "Coach", "test-model", 100, "test-price", [scope])
    cancel_undispatched(call_id)
    cancel_undispatched(call_id)
    assert not mark_dispatching(call_id)
    next_call = reserve(requests[1], "Coach", "test-model", 100, "test-price", [scope])
    assert mark_dispatching(next_call)
    with pytest.raises(ValueError, match="reconciliation"):
        cancel_undispatched(next_call)
    settle(next_call, 0, {"input_tokens": 0, "output_tokens": 0})
    with transaction() as db:
        account = db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == scope))
        assert account.reserved_micros == 0


def test_settlement_persists_output_with_accounting(budget_requests):
    scope, requests = budget_requests
    call_id = reserve(
        requests[0], "Intake", "test-model", 100, "test-price", [scope], request_fingerprint="a" * 64
    )
    assert mark_dispatching(call_id)
    settle(call_id, 20, {"output_tokens": 1}, "test-provider", {"reply": "Hello"})
    with transaction() as db:
        call = db.get(ModelCall, call_id)
        assert call.output == {"reply": "Hello"}
        assert call.request_fingerprint == "a" * 64
        assert call.status == "Settled"
