"""Releasing a stopped local slot never resolves unknown billing or frees money."""

from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from redis import Redis

from app.bootstrap import bootstrap
from app.database import transaction
from app.main import app
from app.models import BudgetAccount, BudgetReservation, ModelCall, Request, WorkItem, now
from app.settings import get_settings


def test_release_slot_preserves_unknown_usage_and_reservation():
    bootstrap()
    settings = get_settings()
    with TestClient(app) as client:
        auth = client.post(
            "/api/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password.get_secret_value()},
        ).json()
        client.headers["x-csrf-token"] = auth["csrf_token"]
        with transaction() as db:
            request = Request(
                user_id=auth["user"]["id"],
                kind="Operations",
                status="Waiting",
                deadline=now() + timedelta(hours=1),
            )
            account = BudgetAccount(scope_key=f"usage-test:{uuid4()}", limit_micros=1000, reserved_micros=100)
            db.add_all([request, account])
            db.flush()
            call = ModelCall(
                request_id=request.id,
                user_id=auth["user"]["id"],
                role="TechnicalManager",
                model="synthetic-test",
                status="UsageUnknown",
                reserved_micros=100,
                price_version="synthetic-test",
                created_at=now() - timedelta(minutes=10),
            )
            db.add(call)
            db.flush()
            db.add(BudgetReservation(account_id=account.id, call_id=call.id, amount_micros=100))
            item = WorkItem(
                request_id=request.id,
                role="TechnicalManager",
                subject_kind="Test",
                subject_id=request.id,
                status="Running",
            )
            db.add(item)
            db.flush()
            call_id, account_id, item_id = call.id, account.id, item.id
        with Redis.from_url(settings.redis_url) as connection:
            connection.sadd("studio:model:active", call_id)
            path = f"/api/owner/unknown-usage/{call_id}/release-slot"
            body = {"reason": "The test task has stopped; preserve its unknown charge reservation."}
            assert client.post(path, json=body).status_code == 409
            with transaction() as db:
                db.get(WorkItem, item_id).status = "Waiting"
            response = client.post(path, json=body)
            assert response.status_code == 200
            assert response.json()["billing_status"] == "UsageUnknown"
            assert not connection.sismember("studio:model:active", call_id)
        with transaction() as db:
            assert db.get(ModelCall, call_id).status == "UsageUnknown"
            assert db.get(ModelCall, call_id).actual_micros is None
            assert db.get(BudgetAccount, account_id).reserved_micros == 100
            assert db.get(BudgetAccount, account_id).spent_micros == 0
