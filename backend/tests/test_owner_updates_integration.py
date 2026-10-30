"""Real inbox lifecycle, authorization and duplicate reconciliation."""

from test_owner_workspace_integration import (
    approval_for,
    model_count,
    pending_application,
    workspace_clients,  # noqa: F401
)

from app.database import transaction
from app.models import Approval


def test_owner_updates_persist_seen_and_resolve_without_model_calls(workspace_clients):  # noqa: F811
    owner, member = workspace_clients
    assessment = pending_application(member)
    approval = approval_for(owner, assessment)
    before = model_count()
    first = owner.get("/api/owner/updates")
    assert first.status_code == 200, first.text
    row = next(r for r in first.json()["items"] if r["item"]["id"] == approval["id"])
    assert row["state"] == "Needs action"
    assert member.get("/api/owner/updates").status_code == 403
    assert member.post(f"/api/owner/updates/{row['id']}/read", json={}).status_code == 403
    assert owner.post(f"/api/owner/updates/{row['id']}/read", json={}).status_code == 200
    again = owner.get("/api/owner/updates").json()
    same = [r for r in again["items"] if r["item"]["id"] == approval["id"]]
    assert len(same) == 1 and same[0]["read_at"]
    assert same[0]["state"] == "Needs action"
    assert again["needs_action"] == first.json()["needs_action"]
    with transaction() as db:
        db.get(Approval, approval["id"]).decision = "Rejected"
    final = owner.get("/api/owner/updates").json()
    resolved = next(r for r in final["items"] if r["id"] == row["id"])
    assert resolved["state"] == "Resolved" and resolved["resolved_at"]
    assert final["needs_action"] == first.json()["needs_action"] - 1
    assert model_count() == before


def test_usage_acknowledgement_survives_sync_and_preserves_money(workspace_clients):  # noqa: F811
    from datetime import timedelta

    from sqlalchemy import func, select

    from app.models import AuditEvent, Incident, ModelCall, Request, now
    from app.owner_workspace import usage_items

    owner, member = workspace_clients
    with transaction() as db:
        user_id = db.scalar(select(Request.user_id).limit(1))
        request = Request(
            user_id=user_id, kind="Combined", status="Cancelled", deadline=now() + timedelta(hours=1)
        )
        db.add(request)
        db.flush()
        call = ModelCall(
            request_id=request.id,
            user_id=user_id,
            role="Nutrition",
            model="synthetic-test",
            status="UsageUnknown",
            reserved_micros=478413,
            price_version="test",
        )
        db.add(call)
        db.flush()
        incident = Incident(
            request_id=request.id,
            title="Usage review",
            event_key="usage-test:" + call.id,
            status="Open",
            evidence={"reason": "UsageReconciliation"},
        )
        db.add(incident)
        db.flush()
        call_id, incident_id = call.id, incident.id
    first = owner.get("/api/owner/updates").json()
    assert not any(r["item"]["id"] == incident_id and r["state"] == "Needs action" for r in first["items"])
    assert next(r for r in first["items"] if r["item"]["id"] == call_id)["state"] == "Needs action"
    path = f"/api/owner/unknown-usage/{call_id}/acknowledge"
    assert member.post(path, json={}).status_code == 403
    for _ in range(2):
        assert owner.post(path, json={}).status_code == 200
        result = owner.get("/api/owner/updates").json()
        assert next(r for r in result["items"] if r["item"]["id"] == call_id)["state"] == "Monitoring"
        assert result["needs_action"] == first["needs_action"] - 1
    with transaction() as db:
        call = db.get(ModelCall, call_id)
        assert call.status == "UsageUnknown" and call.actual_micros is None and call.reserved_micros == 478413
        assert db.get(Incident, incident_id).status == "Open"
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.subject_id == call_id, AuditEvent.event_type == "model.usage_acknowledged")
            )
            == 1
        )
        assert next(i for i in usage_items(db) if i["id"] == call_id)["responsibility"] == "monitoring"
