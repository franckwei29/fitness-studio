"""Readiness failures are reported without leaking connection details."""

from fastapi.testclient import TestClient

import app.main as main


def test_readiness_failure_does_not_affect_liveness(monkeypatch):
    def unavailable():
        raise RuntimeError("Private database URL and credentials")

    monkeypatch.setattr(main, "check_readiness", unavailable)
    with TestClient(main.app) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert "Private" not in response.text
        assert client.get("/health/live").status_code == 200


def test_ready_only_after_dependency_check(monkeypatch):
    checks = []
    monkeypatch.setattr(main, "check_readiness", lambda: checks.append(True))
    with TestClient(main.app) as client:
        assert client.get("/health/ready").json()["status"] == "ready"
    assert checks == [True]
