"""Incident analysis queues an owner-only request with allowlisted evidence."""

from uuid import uuid4

from test_admission_integration import member as member_fixture
from test_admission_integration import owner_client as owner_fixture
from test_work_queue_integration import task as task_fixture

from app.database import transaction
from app.incident_context import incident_context
from app.incidents import record_incident
from app.models import Incident, Request
from app.work_queue import claim_next, wait_for

member = member_fixture
owner_client = owner_fixture
task = task_fixture


def test_incident_analysis_is_owner_only_and_redacted(task, member, owner_client):
    request_id, _, _ = task
    claim = claim_next(request_id=request_id)
    wait_for(claim, "BudgetRequired")
    incident_id = record_incident(claim, "BudgetRequired")
    with transaction() as db:
        incident = db.get(Incident, incident_id)
        incident.title = "PRIVATE-HEALTH-TEXT"
        incident.evidence = incident.evidence | {
            "profile": {"medical_supervision": True},
            "secret": "PRIVATE-KEY",
        }
        context = incident_context(db, incident_id)
        assert context["reason"] == "BudgetRequired"
        assert context["task_status"] == "Waiting"
        assert context["attempts"] == 1
        assert "PRIVATE" not in str(context) and "medical_supervision" not in str(context)
    body = {
        "key": str(uuid4()),
        "purpose": "Operations",
        "content": "Explain the selected incident.",
        "incident_id": incident_id,
    }
    session = member.post("/api/conversations").json()
    assert member.post(f"/api/conversations/{session['id']}/messages", json=body).status_code == 403
    session = owner_client.post("/api/conversations").json()
    endpoint = f"/api/conversations/{session['id']}/messages"
    response = owner_client.post(endpoint, json=body)
    assert response.status_code == 202, response.text
    assert owner_client.post(endpoint, json=body).json() == response.json()
    assert owner_client.post(endpoint, json=body | {"incident_id": str(uuid4())}).status_code == 409
    assert (
        owner_client.post(
            endpoint, json=body | {"key": str(uuid4()), "incident_id": str(uuid4())}
        ).status_code
        == 404
    )
    with transaction() as db:
        request = db.get(Request, response.json()["request_id"])
        assert request.payload["incident_id"] == incident_id and request.model_calls == 0
        assert db.get(Incident, incident_id).status == "Open"
        request.status = "Cancelled"
