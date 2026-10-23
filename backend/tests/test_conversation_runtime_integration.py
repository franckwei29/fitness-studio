"""Operational explanations consume summaries, not member health records."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.admission import fingerprint
from app.agent_contracts import TECHNICAL_INSTRUCTION, ChatReply
from app.bootstrap import bootstrap
from app.database import transaction
from app.main import app
from app.models import AgentVersion, MemberGroup, ModelCall, Request
from app.settings import get_settings
from app.worker import run_once


def test_owner_operational_reply_uses_summary_context(monkeypatch):
    bootstrap()
    contexts = []
    with transaction() as db:
        values = {
            "role": "TechnicalManager",
            "model": get_settings().gemini_model,
            "prompt": TECHNICAL_INSTRUCTION + "\nTest version " + str(uuid4()),
        }
        version = AgentVersion(label="Operations test", fingerprint=fingerprint(values), **values)
        db.add(version)
        db.flush()
        group = MemberGroup(
            name=f"Operations-{uuid4()}", configuration={"agent_versions": {"TechnicalManager": version.id}}
        )
        db.add(group)
        db.flush()
        group_id, version_id = group.id, version.id

    def synthetic_generate(claim, instruction, context, output_type):
        contexts.append(context)
        assert context["profile"] == {}
        assert context["agent_version_id"] == version_id
        assert "request_counts" in context["operations"]
        with transaction() as db:
            request = db.get(Request, claim.request_id)
            call = ModelCall(
                request_id=request.id,
                user_id=request.user_id,
                role=claim.role,
                model="synthetic-test",
                status="Settled",
                reserved_micros=0,
                actual_micros=0,
                price_version="synthetic-test",
            )
            db.add(call)
            db.flush()
            return ChatReply(reply="Some requests are waiting. Review their recorded reasons."), call.id

    monkeypatch.setattr("app.conversation_runtime.generate", synthetic_generate)
    with TestClient(app) as client:
        settings = get_settings()
        auth = client.post(
            "/api/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password.get_secret_value()},
        ).json()
        client.headers["x-csrf-token"] = auth["csrf_token"]
        session = client.post("/api/conversations").json()
        result = client.post(
            f"/api/conversations/{session['id']}/messages",
            json={
                "key": str(uuid4()),
                "purpose": "Operations",
                "operations_ai": True,
                "content": "Explain current request activity.",
                "operations_group_id": group_id,
            },
        ).json()
        assert run_once(result["request_id"])
        history = client.get(f"/api/conversations/{session['id']}").json()
        assert history["requests"][0]["status"] == "Completed"
        assert history["messages"][-1]["sender"] == "TechnicalManager"
        assert len(contexts) == 1
