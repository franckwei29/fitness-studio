"""Overview and downloadable brief share exact period metrics without inference."""

from datetime import datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import transaction
from app.main import app
from app.models import Activation, Incident, Member, MemberGroup, ModelCall, PlanVersion, Request, User
from app.settings import get_settings


def test_period_boundaries_costs_and_brief_match(monkeypatch):
    def no_model(*args, **kwargs):
        raise AssertionError("Reporting must not call a model.")

    monkeypatch.setattr("app.model_gateway.generate", no_model)
    settings = get_settings()
    query = "?start=2001-01-01&end=2001-01-03"
    with TestClient(app) as client:
        auth = client.post(
            "/api/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password.get_secret_value()},
        )
        assert auth.status_code == 200
        before = client.get("/api/owner/dashboard" + query).json()["period"]
        at = datetime(2001, 1, 1)
        with transaction() as db:
            user = User(
                email=f"period-{uuid4()}@example.test",
                display_name="Private display name",
                password_hash="Unused",
            )
            group = MemberGroup(name=f"Period-{uuid4()}", configuration={})
            db.add_all([user, group])
            db.flush()
            member = Member(user_id=user.id, group_id=group.id, created_at=at)
            request = Request(
                user_id=user.id,
                kind="Combined",
                deadline=at + timedelta(days=1),
                payload={"content": "Private conversation text"},
            )
            db.add_all([member, request])
            db.flush()
            db.add(
                Activation(
                    operation_key=str(uuid4()),
                    member_id=member.id,
                    request_id=request.id,
                    result={},
                    created_at=at,
                )
            )
            for index in range(2):
                db.add(
                    PlanVersion(
                        member_id=member.id,
                        request_id=request.id,
                        kind="Training",
                        version=index + 1,
                        status="Validated",
                        profile_version=1,
                        config_version=1,
                        agent_version_id="synthetic",
                        content={},
                        rationale="Synthetic",
                        fingerprint=str(uuid4()),
                        created_at=at,
                    )
                )
            for status, cost, reserved, created in [
                ("Settled", 27, 100, at),
                ("UsageUnknown", None, 100, at + timedelta(days=2, hours=23)),
                ("Settled", 999, 999, at + timedelta(days=3)),
            ]:
                db.add(
                    ModelCall(
                        request_id=request.id,
                        user_id=user.id,
                        role="Coach",
                        model="synthetic-test",
                        status=status,
                        reserved_micros=reserved,
                        actual_micros=cost,
                        price_version="synthetic-test",
                        created_at=created,
                    )
                )
            db.add(
                Incident(
                    event_key=str(uuid4()),
                    title="Unresolved closure",
                    status="Closed",
                    outcome="Unresolved",
                    evidence={},
                )
            )
            db.add(
                Incident(
                    event_key=str(uuid4()),
                    title="Verified recovery",
                    status="Closed",
                    outcome="Resolved",
                    evidence={},
                )
            )
            member_id = member.id
        summary = client.get("/api/owner/dashboard" + query).json()["period"]
        assert summary["new_members"] == before["new_members"] + 1
        assert summary["plan_versions_created"] == before["plan_versions_created"] + 2
        assert summary["successful_activations"] == before["successful_activations"] + 1
        assert summary["settled_micros"] == before["settled_micros"] + 27
        assert summary["held_micros"] == before["held_micros"] + 100
        assert summary["unknown_calls"] == before["unknown_calls"] + 1
        assert summary["unresolved_incidents_now"] == before["unresolved_incidents_now"] + 1
        member_cost = next(row for row in summary["cost_by_member"] if row["member_id"] == member_id)
        assert member_cost == {
            "member_id": member_id,
            "calls": 2,
            "settled_micros": 27,
            "held_micros": 100,
            "unknown_calls": 1,
        }
        report = client.get("/api/owner/operations-brief" + query)
        assert report.status_code == 200
        assert report.headers["content-type"].startswith("text/markdown")
        assert ".md" in report.headers["content-disposition"]
        assert f"| Successful activations | {summary['successful_activations']} |" in report.text
        assert f"| Settled model cost | USD {summary['settled_micros'] / 1_000_000:.6f} |" in report.text
        assert "Private conversation text" not in report.text
        assert "Private display name" not in report.text
        for invalid in (
            "?start=2001-01-03&end=2001-01-01",
            "?start=2000-01-01&end=2002-01-01",
            "?end=2999-01-01",
        ):
            assert client.get("/api/owner/dashboard" + invalid).status_code == 422
            assert client.get("/api/owner/operations-brief" + invalid).status_code == 422
