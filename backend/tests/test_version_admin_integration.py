"""Saving a candidate is owner-only, immutable, and does not publish it."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.bootstrap import bootstrap
from app.main import app
from app.settings import get_settings


def test_candidate_save_deduplicates_without_changing_groups():
    bootstrap()
    settings = get_settings()
    with TestClient(app) as client:
        assert client.get("/api/owner/agent-versions").status_code == 401
        auth = client.post(
            "/api/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password.get_secret_value()},
        ).json()
        client.headers["x-csrf-token"] = auth["csrf_token"]
        before = client.get("/api/owner/groups").json()
        body = {
            "role": "Coach",
            "label": f"Test candidate {uuid4()}",
            "model": settings.gemini_model,
            "instructions": f"Use concise explanations. Test reference {uuid4()}.",
        }
        result = client.post("/api/owner/agent-versions", json=body)
        assert result.status_code == 201
        candidate = result.json()
        assert "write all replies and normalized field values in English" in candidate["prompt"]
        assert body["instructions"] in candidate["prompt"]
        duplicate = client.post("/api/owner/agent-versions", json=body | {"label": "Another label"})
        assert duplicate.json()["id"] == candidate["id"]
        assert duplicate.json()["label"] == body["label"]
        assert client.get("/api/owner/groups").json() == before
        baseline = client.post(
            "/api/owner/agent-versions", json=body | {"instructions": "Baseline " + body["instructions"]}
        ).json()
        comparison = client.post(
            f"/api/owner/agent-versions/{candidate['id']}/evaluations",
            json={"baseline_id": baseline["id"], "evidence_mode": "Live"},
        )
        assert comparison.status_code == 201
        run = comparison.json()
        assert run["status"] == "Prepared" and run["planned_calls"] == 6
        assert run["summary"] is None and len(run["cases"]) == 3
        detail = client.get(f"/api/owner/agent-versions/{candidate['id']}/evaluations/{run['id']}")
        assert detail.json()["results"] == []
        premature_review = client.post(
            f"/api/owner/agent-versions/{candidate['id']}/evaluations/{run['id']}/review",
            json={
                "ratings": [
                    {
                        "result_id": "missing",
                        "explanation_quality": 5,
                        "factual_and_role_compliant": True,
                        "notes": "Premature test review",
                    }
                ]
            },
        )
        assert premature_review.status_code == 409
        assert (
            client.post(
                f"/api/owner/agent-versions/{candidate['id']}/evaluations",
                json={"baseline_id": candidate["id"]},
            ).status_code
            == 422
        )
        listing = client.get("/api/owner/agent-versions").json()["versions"]
        assert next(row for row in listing if row["id"] == candidate["id"])["configured_groups"] == []
        assert client.post("/api/owner/agent-versions", json=body | {"model": "unpriced"}).status_code == 422
        assert client.post("/api/owner/agent-versions", json=body | {"instructions": "  "}).status_code == 422
        assert client.put(f"/api/owner/agent-versions/{candidate['id']}", json=body).status_code in {404, 405}
    with TestClient(app) as client:
        member = client.post(
            "/api/auth/register",
            json={
                "email": f"version-member-{uuid4()}@example.test",
                "password": "Test-password-12345",
                "display_name": "Version Test",
            },
        ).json()
        client.headers["x-csrf-token"] = member["csrf_token"]
        assert client.get("/api/owner/agent-versions").status_code == 403
        assert client.post("/api/owner/agent-versions", json=body).status_code == 403


def test_provider_catalog_options_and_candidate_deduplication():
    bootstrap()
    settings = get_settings()
    with TestClient(app) as client:
        auth = client.post(
            "/api/auth/login",
            json={
                "email": settings.owner_email,
                "password": settings.owner_password.get_secret_value(),
            },
        ).json()
        client.headers["x-csrf-token"] = auth["csrf_token"]
        listing = client.get("/api/owner/agent-versions").json()
        assert {row["provider"] for row in listing["models"]} == {"openai", "gemini"}
        assert all("api_key" not in row for row in listing["models"])
        body = {
            "role": "Coach",
            "label": f"Luna {uuid4()}",
            "provider": "openai",
            "model": "gpt-6-luna",
            "max_output_tokens": 1024,
            "reasoning_effort": "none",
            "instructions": f"Explain clearly {uuid4()}.",
        }
        before = client.get("/api/owner/groups").json()
        candidate = client.post("/api/owner/agent-versions", json=body)
        assert candidate.status_code == 201, candidate.text
        assert candidate.json()["provider"] == "openai" and candidate.json()["max_output_tokens"] == 1024
        assert client.post("/api/owner/agent-versions", json=body).json()["id"] == candidate.json()["id"]
        changed = client.post("/api/owner/agent-versions", json=body | {"max_output_tokens": 512})
        assert changed.json()["fingerprint"] != candidate.json()["fingerprint"]
        assert client.get("/api/owner/groups").json() == before
        assert client.post("/api/owner/agent-versions", json=body | {"provider": "gemini"}).status_code == 422
        assert (
            client.post(
                "/api/owner/agent-versions",
                json=body | {"model": settings.gemini_model, "provider": "gemini"},
            ).status_code
            == 422
        )
