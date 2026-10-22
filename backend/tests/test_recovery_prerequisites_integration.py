"""Both exact approved execution receipts are required before task recovery."""

from datetime import timedelta

from test_admission_integration import owner_client as owner_fixture
from test_work_queue_integration import task as task_fixture

from app.admission import fingerprint
from app.database import transaction
from app.incidents import record_incident
from app.models import Approval, ChangeRequest, Incident, WorkItem, now
from app.work_queue import claim_next, wait_for

owner_client = owner_fixture
task = task_fixture


def test_configuration_and_release_receipts_both_gate_recovery(task, owner_client):
    request_id, work_id, _ = task
    claim = claim_next(request_id=request_id)
    wait_for(claim, "PolicyReview")
    incident_id = record_incident(claim, "PolicyReview")
    identifiers = []
    with transaction() as db:
        group_id = db.get(WorkItem, work_id).input_versions["group_id"]
        for kind in ["GroupConfiguration", "AgentRelease"]:
            baseline = {"version": 1, "configuration": {}}
            candidate = {"role": "Intake"} if kind == "AgentRelease" else {}
            change = ChangeRequest(
                kind=kind,
                group_id=group_id,
                baseline=baseline,
                candidate=candidate,
                fingerprint=fingerprint({"group_id": group_id, "baseline": baseline, "candidate": candidate}),
                status="Approved",
                reason="Synthetic prerequisite protocol test",
            )
            db.add(change)
            db.flush()
            identifiers.append(change.id)
            db.add(
                Approval(
                    subject_kind=kind,
                    subject_id=change.id,
                    subject_version=1,
                    fingerprint=change.fingerprint,
                    decision="Approved",
                    scope={},
                    context={},
                    expires_at=now() + timedelta(hours=1),
                )
            )
    base = f"/api/owner/incidents/{incident_id}"
    r = owner_client.post(
        base + "/prerequisites", json={"expected_fence": claim.fence, "change_ids": identifiers}
    )
    assert r.status_code == 200, r.text
    body = {"expected_fence": claim.fence, "reason": "Verify every required execution before recovery."}
    assert owner_client.post(base + "/request-recovery", json=body).status_code == 409
    with transaction() as db:
        row = db.get(ChangeRequest, identifiers[0])
        row.status = "Executed"
        row.execution = {"status": "Committed", "group_version": 2}
    assert owner_client.post(base + "/request-recovery", json=body).status_code == 409
    with transaction() as db:
        row = db.get(ChangeRequest, identifiers[1])
        row.status = "Canary"
        row.execution = {"status": "Committed", "group_version": 3}
    r = owner_client.post(base + "/request-recovery", json=body)
    assert r.status_code == 200, r.text
    assert len(r.json()["recovery"]["prerequisite_receipts"]) == 2
    with transaction() as db:
        row = db.get(ChangeRequest, identifiers[1])
        row.status = "RollbackBlocked"
    assert claim_next(request_id=request_id) is None
    with transaction() as db:
        assert db.get(WorkItem, work_id).status == "Waiting"
        assert db.get(Incident, incident_id).outcome != "Recovered"
