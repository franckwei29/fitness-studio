"""Incident analysis queues an owner-only request with allowlisted evidence."""

from uuid import uuid4

from test_admission_integration import member as member_fixture
from test_admission_integration import owner_client as owner_fixture
from test_work_queue_integration import task as task_fixture

from app.database import transaction
from app.incident_context import IncidentAnalysis, incident_context
from app.incidents import record_incident
from app.models import Incident, ModelCall, Request
from app.work_queue import claim_next, wait_for
from app.worker import run_once

member = member_fixture
owner_client = owner_fixture
task = task_fixture


def test_incident_analysis_is_owner_only_and_redacted(task, member, owner_client, monkeypatch):
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

    def synthetic(claim, instruction, context, contract):
        assert contract is IncidentAnalysis and context["incident"]["id"] == incident_id
        assert context["profile"] == {} and "PRIVATE" not in str(context["incident"])
        output = contract(
            observed_facts=["The task is waiting for budget."],
            possible_causes=["The configured budget may be insufficient."],
            proposed_actions=["Inspect the budget before requesting recovery."],
        )
        with transaction() as db:
            request = db.get(Request, claim.request_id)
            request.model_calls += 1
            call = ModelCall(
                request_id=request.id,
                user_id=request.user_id,
                role=claim.role,
                model="synthetic-test",
                status="Settled",
                reserved_micros=0,
                actual_micros=0,
                price_version="synthetic",
                output=output.model_dump(),
            )
            db.add(call)
            db.flush()
            return output, call.id

    monkeypatch.setattr("app.conversation_runtime.generate", synthetic)
    assert run_once(response.json()["request_id"])
    with transaction() as db:
        request = db.get(Request, response.json()["request_id"])
        assert request.payload["incident_id"] == incident_id and request.model_calls == 1
        assert request.status == "Completed"
        assert db.get(Incident, incident_id).status == "Open"
