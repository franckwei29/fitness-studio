"""Operational records are owner-only and omit conversational payloads."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.bootstrap import bootstrap
from app.main import app
from app.settings import get_settings


def test_dashboard_requires_owner_and_trace_omits_messages():
    bootstrap()
    with TestClient(app) as member, TestClient(app) as owner:
        result = member.post(
            "/api/auth/register",
            json={
                "email": f"ops-{uuid4()}@example.test",
                "password": "Integration-test-123!",
                "display_name": "Operations Test",
            },
        ).json()
        member.headers["x-csrf-token"] = result["csrf_token"]
        assert member.get("/api/owner/dashboard").status_code == 403
        assert member.get("/api/owner/operations-brief").status_code == 403
        session = member.post("/api/conversations", json={}).json()
        result = member.post(
            f"/api/conversations/{session['id']}/messages",
            json={"content": "Private health conversation", "key": str(uuid4())},
        ).json()
        settings = get_settings()
        assert (
            owner.post(
                "/api/auth/login",
                json={"email": settings.owner_email, "password": settings.owner_password.get_secret_value()},
            ).status_code
            == 200
        )
        dashboard = owner.get("/api/owner/dashboard")
        assert dashboard.status_code == 200
        assert "budgets" in dashboard.json()
        trace_url = f"/api/owner/requests/{result['request_id']}/trace"
        assert member.get(trace_url).status_code == 403
        trace = owner.get(trace_url)
        assert trace.status_code == 200
        assert "Private health conversation" not in trace.text
        assert "payload" not in trace.json()["request"]
        assert trace.json()["tasks"][0]["role"] == "Intake"
