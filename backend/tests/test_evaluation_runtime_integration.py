"""Queued evaluation is idempotent and cannot accept synthetic calls as live evidence."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.agent_contracts import ChatReply
from app.bootstrap import bootstrap
from app.database import transaction
from app.main import app
from app.models import ModelCall, Request
from app.settings import get_settings
from app.worker import run_once


def test_evaluation_queue_and_synthetic_evidence_rejection(monkeypatch):
    bootstrap()
    seen = []

    def synthetic(claim, instruction, context, output_type, max_output_tokens):
        seen.append(context)
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
            return ChatReply(reply="Test response"), call.id

    monkeypatch.setattr("app.evaluation_runtime.generate", synthetic)
    with TestClient(app) as client:
        settings = get_settings()
        auth = client.post(
            "/api/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password.get_secret_value()},
        ).json()
        client.headers["x-csrf-token"] = auth["csrf_token"]
        ids = []
        for label in ("Baseline", "Candidate"):
            version = client.post(
                "/api/owner/agent-versions",
                json={
                    "role": "TechnicalManager",
                    "label": label,
                    "model": settings.gemini_model,
                    "instructions": f"{label} test {uuid4()}",
                },
            ).json()
            ids.append(version["id"])
        path = f"/api/owner/agent-versions/{ids[1]}/evaluations"
        run = client.post(path, json={"baseline_id": ids[0]}).json()
        start = f"{path}/{run['id']}/start"
        queued = client.post(start).json()
        assert len(queued["summary"]["request_ids"]) == 4
        assert client.post(start).json()["summary"] == queued["summary"]
        request_id = queued["summary"]["request_ids"][0]
        assert run_once(request_id)
        detail = client.get(f"{path}/{run['id']}").json()
        assert detail["results"] == []
        assert detail["usage"] == {"calls": 1, "settled_micros": 0, "unknown_calls": 0}
        assert len(detail["tasks"]) == 4
        assert any(task["status"] == "Waiting" for task in detail["tasks"])
        assert detail["run"]["summary"]["release_eligible"] is False
        with transaction() as db:
            assert db.get(Request, request_id).status == "Waiting"
            # Stop unused synthetic test tasks so a later worker cannot run them live.
            from sqlalchemy import select

            from app.models import EvaluationRun, WorkItem

            db.get(EvaluationRun, run["id"]).status = "Stopped"
            for identifier in queued["summary"]["request_ids"]:
                db.get(Request, identifier).status = "Stopped"
                for item in db.scalars(select(WorkItem).where(WorkItem.request_id == identifier)):
                    item.status = "Stopped"
        assert len(seen) == 1 and seen[0]["agent_version_id"] == ids[0]
