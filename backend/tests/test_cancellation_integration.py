"""User cancellation fences workers without discarding billable call evidence."""

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_chat_integration import register

from app.budget import BudgetDenied, mark_dispatching, reserve, settle
from app.database import transaction
from app.main import app
from app.models import BudgetAccount, BudgetReservation, ModelCall, Request, RequestEvent, WorkItem, now
from app.work_queue import Claim, StaleClaim, claim_next, lock_claim


@pytest.mark.parametrize("status", ["Queued", "Running", "Waiting", "AwaitingConfirmation"])
def test_cancel_is_owned_idempotent_and_fences_work(status):
    with TestClient(app) as client, TestClient(app) as other:
        register(client)
        register(other)
        conversation = client.post("/api/conversations").json()["id"]
        submitted = client.post(
            f"/api/conversations/{conversation}/messages",
            json={
                "content": "Explain my options",
                "key": str(uuid4()),
                "purpose": "Chat",
            },
        ).json()
        identifier = submitted["request_id"]
        with transaction() as db:
            request = db.get(Request, identifier)
            request.status = status
            item = db.scalar(select(WorkItem).where(WorkItem.request_id == identifier))
            item.status = "Completed" if status == "AwaitingConfirmation" else status
            item.fence = 1
            item.lease_until = now() + timedelta(minutes=2)
            claim = Claim(item.id, identifier, item.role, 1)
            call = ModelCall(
                request_id=identifier,
                user_id=request.user_id,
                role="Intake",
                model="synthetic",
                status="Dispatching",
                reserved_micros=100,
                price_version="synthetic",
            )
            reserved = ModelCall(
                request_id=identifier,
                user_id=request.user_id,
                role="Intake",
                model="synthetic",
                status="Reserved",
                reserved_micros=100,
                price_version="synthetic",
            )
            db.add_all([call, reserved])
            db.flush()
            call_id, reserved_id = call.id, reserved.id
            account = BudgetAccount(
                scope_key=f"cancellation-test:{uuid4()}", limit_micros=1000, reserved_micros=100
            )
            db.add(account)
            db.flush()
            account_id = account.id
            db.add(BudgetReservation(account_id=account.id, call_id=reserved.id, amount_micros=100))
        url = f"/api/requests/{identifier}/cancel"
        assert other.post(url, json={}).status_code == 404
        response = client.post(url, json={})
        assert response.status_code == 200 and response.json()["status"] == "Cancelled"
        assert "may still incur usage" in response.json()["status_message"]
        assert client.post(url, json={}).json()["status"] == "Cancelled"
        assert any(row["id"] == identifier for row in client.get("/api/requests").json())
        assert all(row["id"] != identifier for row in other.get("/api/requests").json())
        assert claim_next(request_id=identifier) is None
        with transaction() as db:
            with pytest.raises(StaleClaim):
                lock_claim(db, claim)
            assert db.get(ModelCall, call_id).status == "Dispatching"
            assert db.get(ModelCall, reserved_id).status == "Cancelled"
            assert db.get(ModelCall, reserved_id).actual_micros == 0
            assert db.get(BudgetAccount, account_id).reserved_micros == 0
            assert db.get(BudgetAccount, account_id).spent_micros == 0
            events = list(
                db.scalars(
                    select(RequestEvent).where(
                        RequestEvent.request_id == identifier, RequestEvent.event_type == "cancelled"
                    )
                )
            )
            assert len(events) == 1
        with pytest.raises(StaleClaim):
            mark_dispatching(reserved_id, claim=claim)
        with pytest.raises(BudgetDenied):
            reserve(identifier, "Intake", "synthetic", 100, "synthetic", ["global"])
        # A provider result already in flight still settles, without reviving work.
        settle(call_id, 17, {"synthetic": True}, output={"reply": "Synthetic late reply"})
        assert client.get(f"/api/requests/{identifier}").json()["status"] == "Cancelled"
        assert len(client.get(f"/api/conversations/{conversation}").json()["messages"]) == 1


def test_completed_request_cannot_be_cancelled():
    with TestClient(app) as client:
        register(client)
        conversation = client.post("/api/conversations").json()["id"]
        identifier = client.post(
            f"/api/conversations/{conversation}/messages",
            json={
                "content": "Explain my options",
                "key": str(uuid4()),
            },
        ).json()["request_id"]
        with transaction() as db:
            db.get(Request, identifier).status = "Completed"
        assert client.post(f"/api/requests/{identifier}/cancel", json={}).status_code == 409
