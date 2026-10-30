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
