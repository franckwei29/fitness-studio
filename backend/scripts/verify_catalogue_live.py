"""Opt-in smoke test against an isolated DB, using normal request authorization and budgets."""

import json
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.catalogue_resolver import resolve_exercise, resolve_food
from app.database import engine, transaction
from app.main import app
from app.models import ModelCall
from app.rules import EXCLUSIONS
from app.settings import get_settings
from app.work_queue import claim_next


def main():
    assert engine().url.database == "fitness_expanded_test", "Use the disposable test database only."
    assert get_settings().model_calls_enabled
    with TestClient(app) as client:
        auth = client.post(
            "/api/auth/register",
            json={
                "email": f"catalogue-live-{uuid4()}@example.test",
                "password": uuid4().hex + "!Ab",
                "display_name": "Catalogue verification",
            },
        )
        assert auth.status_code == 201, auth.status_code
        client.headers["x-csrf-token"] = auth.json()["csrf_token"]
        assessment = client.post("/api/assessments").json()
        facts = dict(
            age=28,
            weight_kg=72.5,
            goal="General fitness",
            experience="Beginner",
            available_days=3,
            session_minutes=60,
            equipment=[],
            dietary_restrictions=[],
            **{k: False for k in EXCLUSIONS},
        )
        result = client.post(
            f"/api/assessments/{assessment['id']}/confirm",
            json={"expected_version": assessment["version"], "facts": facts},
        )
        assert result.json()["status"] == "Accepted"
        session = client.post("/api/conversations").json()
        for kind, query, resolve in [
            ("Nutrition", "Cooked teff grain with water, no added oil", resolve_food),
            ("Training", "Underwater hockey recreational", resolve_exercise),
        ]:
            request = client.post(
                f"/api/conversations/{session['id']}/messages",
                json={"key": str(uuid4()), "purpose": kind, "content": "Please include " + query},
            )
            assert request.status_code == 202, request.status_code
            identifier = request.json()["request_id"]
            try:
                claim = claim_next(lease_seconds=600, request_id=identifier)
                assert claim is not None
                entry = resolve(claim, query, facts)
                with transaction() as db:
                    before = len(
                        list(db.scalars(select(ModelCall).where(ModelCall.request_id == identifier)))
                    )
                again = resolve(claim, query, facts)
                with transaction() as db:
                    calls = list(db.scalars(select(ModelCall).where(ModelCall.request_id == identifier)))
                    report = {
                        "kind": kind,
                        "name": entry.name,
                        "id": entry.id,
                        "origin": entry.origin,
                        "cache_hit": again.id == entry.id and len(calls) == before,
                        "calls": len(calls),
                        "statuses": [c.status for c in calls],
                        "cost_micros": sum(c.actual_micros or 0 for c in calls),
                    }
                print(json.dumps(report), flush=True)
            except Exception as exc:
                print(
                    json.dumps({"kind": kind, "error_type": type(exc).__name__, "reason": str(exc)}),
                    flush=True,
                )
            finally:
                client.post(f"/api/requests/{identifier}/cancel", json={})


if __name__ == "__main__":
    main()
