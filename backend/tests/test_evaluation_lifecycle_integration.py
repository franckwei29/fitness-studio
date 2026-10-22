"""Cancelled and expired comparison batches stop without manufacturing evidence."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_admission_integration import owner_client as owner_fixture

from app.database import transaction
from app.evaluation_lifecycle import close_incomplete_evaluations
from app.expiry import expire_waits
from app.models import AuditEvent, EvaluationRun, Request, WorkItem, now
from app.settings import get_settings

owner_client = owner_fixture


@pytest.mark.parametrize("ending", ["cancelled", "expired", "partial", "missing_results"])
def test_comparison_terminal_state_preserves_evidence(owner_client, ending):
    ids = []
    for label in ("Baseline", "Candidate"):
        response = owner_client.post(
            "/api/owner/agent-versions",
            json={
                "role": "TechnicalManager",
                "label": label,
                "model": get_settings().gemini_model,
                "instructions": f"Synthetic lifecycle test {uuid4()}",
            },
        )
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    path = f"/api/owner/agent-versions/{ids[1]}/evaluations"
    prepared = owner_client.post(path, json={"baseline_id": ids[0]}).json()
    detail_path = f"{path}/{prepared['id']}"
    queued = owner_client.post(detail_path + "/start").json()
    requests = queued["summary"]["request_ids"]
    assert len(requests) == 4
    if ending in {"cancelled", "partial"}:
        for identifier in requests if ending == "cancelled" else requests[:1]:
            response = owner_client.post(f"/api/requests/{identifier}/cancel")
            assert response.status_code == 200, response.text
    elif ending == "expired":
        with transaction() as db:
            for identifier in requests:
                db.get(Request, identifier).deadline = now() - timedelta(minutes=1)
        expire_waits()
    else:
        with transaction() as db:
            for identifier in requests:
                db.get(Request, identifier).status = "Completed"
            for task in db.scalars(select(WorkItem).where(WorkItem.request_id.in_(requests))):
                task.status = "Completed"
    close_incomplete_evaluations()
    close_incomplete_evaluations()
    detail = owner_client.get(detail_path).json()
    assert detail["results"] == []
    assert detail["usage"] == {"calls": 0, "settled_micros": 0, "unknown_calls": 0}
    assert detail["run"]["summary"]["release_eligible"] is False
    assert detail["run"]["status"] == ("Running" if ending == "partial" else "Incomplete")
    if ending == "partial":
        for identifier in requests[1:]:
            assert owner_client.post(f"/api/requests/{identifier}/cancel").status_code == 200
        close_incomplete_evaluations()
    with transaction() as db:
        run = db.get(EvaluationRun, prepared["id"])
        assert run.status == "Incomplete" and run.completed_at is not None
        assert (
            len(
                list(
                    db.scalars(
                        select(AuditEvent).where(
                            AuditEvent.subject_id == run.id, AuditEvent.event_type == "evaluation.incomplete"
                        )
                    )
                )
            )
            == 1
        )
    # Starting an ended comparison cannot silently dispatch another batch.
    restarted = owner_client.post(detail_path + "/start").json()
    assert restarted["status"] == "Incomplete"
    assert restarted["summary"]["request_ids"] == requests
