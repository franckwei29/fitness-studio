"""Identity integration checks against the configured MySQL database."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def test_register_session_csrf_logout_and_login():
    with TestClient(app) as client:
        body = {
            "email": f"test-{uuid4()}@example.test",
            "password": "Integration-test-123!",
            "display_name": "Test Member",
        }
        assert client.get("/api/auth/me").status_code == 401
        response = client.post("/api/auth/register", json=body)
        assert response.status_code == 201
        result = response.json()
        assert result["user"]["role"] == "Member"
        assert client.get("/api/auth/me").json()["user"]["id"] == result["user"]["id"]
        assert client.post("/api/auth/logout").status_code == 403
        assert (
            client.post("/api/auth/logout", headers={"x-csrf-token": result["csrf_token"]}).status_code == 200
        )
        assert client.get("/api/auth/me").status_code == 401
        assert (
            client.post(
                "/api/auth/login", json={"email": body["email"], "password": "incorrect-pass"}
            ).status_code
            == 401
        )
        assert client.post("/api/auth/login", json=body).status_code == 200
        assert client.post("/api/auth/register", json=body).status_code == 409
