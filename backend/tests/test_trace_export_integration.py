"""Privacy, explicit export, immutable review, and conservative remote outcomes."""

import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from test_chat_integration import register

from app.database import transaction
from app.main import app
from app.models import AuditEvent, ModelCall, Request, RequestEvent, WorkItem
from app.pricing import FLASH_LITE
from app.settings import get_settings

PRIVATE = "SYNTHETIC_PRIVATE_RECORD_DO_NOT_EXPORT"


def setup_trace(member, owner):
    register(member)
    settings = get_settings()
    auth = owner.post(
        "/api/auth/login",
        json={"email": settings.owner_email, "password": settings.owner_password.get_secret_value()},
    ).json()
    owner.headers["x-csrf-token"] = auth["csrf_token"]
    session = member.post("/api/conversations", json={}).json()["id"]
    identifier = member.post(
        f"/api/conversations/{session}/messages",
        json={
            "content": PRIVATE,
            "key": str(uuid4()),
        },
    ).json()["request_id"]
    with transaction() as db:
        request = db.get(Request, identifier)
        item = db.scalar(select(WorkItem).where(WorkItem.request_id == identifier))
        item.input_versions = {
            "profile": 3,
            "group_id": str(uuid4()),
            "profile_data": {"goal": PRIVATE},
            "agent_version": PRIVATE,
        }
        db.add(
            ModelCall(
                request_id=identifier,
                user_id=request.user_id,
                role="Intake",
                model=FLASH_LITE.model,
                status="UsageUnknown",
                reserved_micros=100,
                price_version=FLASH_LITE.version,
                usage={"total_input_tokens": 12, "private_metadata": PRIVATE},
                provider_id=PRIVATE,
                output={"reply": PRIVATE},
            )
        )
        db.add(
            AuditEvent(
                actor_id=request.user_id,
                request_id=identifier,
                event_type="model.response_failed",
                details={
                    "http_status": 503,
                    "reason": PRIVATE,
                    "usage": {"private": PRIVATE},
                    "credential": PRIVATE,
                },
            )
        )
        db.add(
            RequestEvent(
                request_id=identifier,
                event_type="waiting",
                payload={"status": "Waiting", "reason": PRIVATE, "candidate": {"goal": PRIVATE}},
            )
        )
    return identifier


def test_local_trace_is_allowlisted_and_external_export_is_optional(monkeypatch):
    monkeypatch.setattr(get_settings(), "studio_langsmith_export_enabled", False)
    with TestClient(app) as member, TestClient(app) as owner:
        identifier = setup_trace(member, owner)
        base = f"/api/owner/requests/{identifier}/trace"
        snapshot = owner.get(base).json()
        assert PRIVATE not in json.dumps(snapshot)
        assert snapshot["calls"][0]["actual_micros"] is None
        assert snapshot["calls"][0]["reserved_micros"] == 100
        assert snapshot["calls"][0]["usage"] == {"total_input_tokens": 12}
        assert snapshot["tasks"][0]["input_versions"]["profile"] == 3
        assert snapshot["events"][0]["details"] == {"http_status": 503}
        assert snapshot["progress"][-1]["details"] == {"status": "Waiting"}
        download = owner.get(base + "/export")
        assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
        assert PRIVATE not in download.text
        assert download.json()["fingerprint"] == snapshot["fingerprint"]
        assert member.get(base + "/export").status_code == 403
        assert (
            member.post(base + "/langsmith", json={"fingerprint": snapshot["fingerprint"]}).status_code == 403
        )
        assert (
            owner.post(base + "/langsmith", json={"fingerprint": snapshot["fingerprint"]}).status_code == 503
        )


@pytest.mark.parametrize(
    "outcome, expected", [(202, "Accepted"), (400, "Failed"), (503, "Unknown"), ("timeout", "Unknown")]
)
def test_explicit_snapshot_export_is_single_attempt_and_preserves_business(monkeypatch, outcome, expected):
    settings = get_settings()
    monkeypatch.setattr(settings, "studio_langsmith_export_enabled", True)
    monkeypatch.setattr(settings, "studio_langsmith_api_key", SecretStr("synthetic-trace-key"))
    with TestClient(app) as member, TestClient(app) as owner:
        identifier = setup_trace(member, owner)
        base = f"/api/owner/requests/{identifier}/trace"
        snapshot = owner.get(base).json()
        dispatches = []

        class Remote:
            def __init__(self, **kwargs):
                assert kwargs == {"timeout": 10, "follow_redirects": False, "trust_env": False}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, *, json, headers):
                dispatches.append(json)
                assert url == "https://api.smith.langchain.com/runs"
                assert headers["x-api-key"] == "synthetic-trace-key"
                if outcome == "timeout":
                    raise httpx.ReadTimeout(PRIVATE)
                return httpx.Response(outcome)

        monkeypatch.setattr("app.trace_export.httpx.Client", Remote)
        assert owner.post(base + "/langsmith", json={"fingerprint": "0" * 64}).status_code == 409
        assert dispatches == []
        response = owner.post(base + "/langsmith", json={"fingerprint": snapshot["fingerprint"]})
        assert response.status_code == 200 and response.json()["status"] == expected
        assert (
            owner.post(base + "/langsmith", json={"fingerprint": snapshot["fingerprint"]}).json()
            == response.json()
        )
        assert len(dispatches) == 1
        assert PRIVATE not in json.dumps(dispatches) and PRIVATE not in response.text
        assert dispatches[0]["inputs"] == dispatches[0]["outputs"] == {}
        with transaction() as db:
            request = db.get(Request, identifier)
            assert request.status == "Queued" and request.model_calls == 0
        assert owner.get(base + "/export").status_code == 200
