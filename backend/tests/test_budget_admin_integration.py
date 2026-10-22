"""Budget edits require fresh evidence and cannot erase existing consumption."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.bootstrap import bootstrap
from app.database import transaction
from app.main import app
from app.models import MemberGroup
from app.settings import get_settings


def test_budget_limit_creation_and_stale_edit_rejection():
    bootstrap()
    with transaction() as db:
        group = MemberGroup(name=f"Budget-{uuid4()}", configuration={})
        db.add(group)
        db.flush()
        scope = f"group:{group.id}"
    with TestClient(app) as client:
        settings = get_settings()
        auth = client.post(
            "/api/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password.get_secret_value()},
        ).json()
        client.headers["x-csrf-token"] = auth["csrf_token"]
        body = {"scope_key": scope, "limit_micros": 1000000, "reason": "Test allowance"}
        created = client.post("/api/owner/budgets", json=body)
        assert created.status_code == 200
        original = created.json()
        assert original["spent_micros"] == original["reserved_micros"] == 0
        update = body | {"limit_micros": 500000, "expected_fingerprint": original["fingerprint"]}
        assert client.post("/api/owner/budgets", json=update).status_code == 200
        assert client.post("/api/owner/budgets", json=update).status_code == 409
        assert client.post("/api/owner/budgets", json=body | {"scope_key": "role:Unknown"}).status_code == 422
