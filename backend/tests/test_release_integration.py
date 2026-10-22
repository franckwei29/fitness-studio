"""Initial canary proposals cannot bypass evaluated evidence or target restrictions."""

from fastapi.testclient import TestClient

from app.bootstrap import bootstrap
from app.main import app
from app.settings import get_settings


def test_release_requires_reviewed_evidence_and_canary_target():
    bootstrap()
    with TestClient(app) as client:
        assert client.get("/api/owner/releases").status_code == 401
        settings = get_settings()
        auth = client.post(
            "/api/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password.get_secret_value()},
        ).json()
        client.headers["x-csrf-token"] = auth["csrf_token"]
        groups = client.get("/api/owner/groups").json()
        canary = next(group for group in groups if group["name"] == "Canary")
        default = next(group for group in groups if group["name"] == "Default")
        payload = {
            "group_id": canary["id"],
            "expected_group_version": canary["version"],
            "evaluation_id": "missing-evaluation",
            "reason": "Test evidence gate",
        }
        assert client.post("/api/owner/releases", json=payload).status_code == 409
        assert (
            client.post(
                "/api/owner/releases",
                json=payload | {"group_id": default["id"], "expected_group_version": default["version"]},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/owner/releases", json=payload | {"expected_group_version": canary["version"] + 1}
            ).status_code
            == 409
        )
        assert client.post("/api/owner/releases/missing/execute", json={}).status_code == 404
        assert client.get("/api/owner/groups").json() == groups
