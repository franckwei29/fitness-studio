"""Disabled models produce a durable wait without dispatching any request."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from app.database import transaction
from app.models import ChatSession, ModelCall, Request, User, WorkItem, now
from app.worker import run_once


def test_disabled_model_wait_is_not_a_completed_chat(monkeypatch):
    from app.settings import get_settings

    settings = get_settings().model_copy(update={"model_calls_enabled": False})
    monkeypatch.setattr("app.model_gateway.get_settings", lambda: settings)
    with transaction() as db:
        user = User(
            email=f"worker-disabled-{uuid4()}@example.test",
            display_name="Worker Test",
            password_hash="Unused",
        )
        db.add(user)
        db.flush()
        session = ChatSession(user_id=user.id)
        db.add(session)
        db.flush()
        request = Request(
            user_id=user.id,
            session_id=session.id,
            kind="Chat",
            deadline=now() + timedelta(minutes=10),
            payload={"content": "Hello"},
        )
        db.add(request)
        db.flush()
        item = WorkItem(
            request_id=request.id, role="Intake", subject_kind="Conversation", subject_id=session.id
        )
        db.add(item)
        db.flush()
        request_id, work_id = request.id, item.id
    assert run_once(request_id)
    assert not run_once(request_id)
    with transaction() as db:
        assert db.get(Request, request_id).status == "Waiting"
        assert db.get(WorkItem, work_id).wait_reason == "ModelDispatchUnavailable"
        assert (
            db.scalar(select(func.count()).select_from(ModelCall).where(ModelCall.request_id == request_id))
            == 0
        )


def test_waiting_incident_cannot_be_claimed_recovered(monkeypatch):
    from fastapi.testclient import TestClient

    from app.bootstrap import bootstrap
    from app.main import app
    from app.models import Incident
    from app.settings import get_settings

    bootstrap()
    with transaction() as db:
        incident = Incident(event_key=str(uuid4()), title="Unresolved test", evidence={}, status="Open")
        db.add(incident)
        db.flush()
        identifier = incident.id
    with TestClient(app) as client:
        settings = get_settings()
        auth = client.post(
            "/api/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password.get_secret_value()},
        ).json()
        client.headers["x-csrf-token"] = auth["csrf_token"]
        assert client.post(f"/api/owner/incidents/{identifier}/verify-recovery").status_code == 409
        closed = client.post(
            f"/api/owner/incidents/{identifier}/close-unresolved",
            json={"reason": "No recovery evidence is available."},
        )
        assert closed.status_code == 200
        assert closed.json()["outcome"] == "Unresolved"
        assert closed.json()["recovery"]["service_restored"] is False
