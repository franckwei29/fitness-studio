"""Concurrent task claims and fenced crash recovery on real MySQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest

from app.database import transaction
from app.models import ModelCall, Request, User, WorkItem, now
from app.work_queue import StaleClaim, claim_next, heartbeat, recover_expired, wait_for


@pytest.fixture
def task():
    with transaction() as db:
        user = User(
            email=f"worker-{uuid4()}@example.test", display_name="Worker Test", password_hash="Unused"
        )
        db.add(user)
        db.flush()
        request = Request(user_id=user.id, kind="Chat", deadline=now() + timedelta(minutes=10))
        db.add(request)
        db.flush()
        item = WorkItem(request_id=request.id, role="Intake", subject_kind="Test", subject_id=user.id)
        db.add(item)
        db.flush()
        return request.id, item.id, user.id


def expire(work_id):
    with transaction() as db:
        db.get(WorkItem, work_id).lease_until = now() - timedelta(seconds=1)


def test_one_claim_wins_and_old_fence_cannot_continue(task):
    request_id, work_id, _ = task
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: claim_next(request_id=request_id), range(4)))
    winners = [claim for claim in claims if claim]
    assert len(winners) == 1
    original = winners[0]
    heartbeat(original)
    expire(work_id)
    recover_expired()
    replacement = claim_next(request_id=request_id)
    assert replacement.fence > original.fence
    with pytest.raises(StaleClaim):
        wait_for(original, "Budget")
    wait_for(replacement, "Budget")
    with transaction() as db:
        assert db.get(Request, request_id).status == "Waiting"
        assert db.get(WorkItem, work_id).attempts == 2


def test_interrupted_provider_call_requires_reconciliation(task):
    request_id, work_id, user_id = task
    claim = claim_next(request_id=request_id)
    with transaction() as db:
        call = ModelCall(
            request_id=request_id,
            user_id=user_id,
            role="Intake",
            model="test-model",
            status="Dispatching",
            reserved_micros=100,
            price_version="test-price",
        )
        db.add(call)
        db.flush()
        call_id = call.id
    expire(work_id)
    recover_expired()
    assert claim_next(request_id=request_id) is None
    with transaction() as db:
        assert db.get(ModelCall, call_id).status == "UsageUnknown"
        assert db.get(WorkItem, work_id).wait_reason == "UsageReconciliation"
    with pytest.raises(StaleClaim):
        heartbeat(claim)


def test_attempts_remain_bounded_across_recovery(task):
    request_id, work_id, _ = task
    for _ in range(3):
        assert claim_next(request_id=request_id)
        expire(work_id)
        recover_expired()
    assert claim_next(request_id=request_id) is None
    with transaction() as db:
        assert db.get(WorkItem, work_id).status == "Stopped"
        assert db.get(WorkItem, work_id).attempts == 3
