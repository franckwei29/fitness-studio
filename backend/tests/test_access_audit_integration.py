"""Denied authenticated access is audited without input text or secrets."""

from sqlalchemy import select
from test_activation_integration import candidate as candidate_fixture
from test_admission_integration import member as member_fixture

from app.database import transaction
from app.models import AuditEvent

member = member_fixture
candidate = candidate_fixture


def test_access_rejections_are_redacted_and_persisted(member, candidate):
    actor_id = member.get("/api/auth/me").json()["user"]["id"]
    assert member.get("/api/owner/incidents").status_code == 403
    assert member.get(f"/api/requests/{candidate['request_id']}").status_code == 404
    assert (
        member.post(
            "/api/conversations",
            headers={"x-csrf-token": "PRIVATE-INVALID-TOKEN"},
            json={"private": "PRIVATE-HEALTH-TEXT"},
        ).status_code
        == 403
    )
    with transaction() as db:
        records = list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.actor_id == actor_id, AuditEvent.event_type == "access.rejected")
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
        assert len(records) == 3
        assert {(row.details["route"], row.details["method"], row.details["status"]) for row in records} == {
            ("/api/owner/incidents", "GET", 403),
            ("/api/requests/{identifier}", "GET", 404),
            ("/api/conversations", "POST", 403),
        }
        assert all(set(row.details) == {"route", "method", "status"} for row in records)
        assert "PRIVATE" not in str([row.details for row in records])
