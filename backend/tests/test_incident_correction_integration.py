"""Recovery requires complete, evidenced correction chains rather than hidden failures."""

from datetime import timedelta
from uuid import uuid4

import pytest
from test_admission_integration import owner_client as owner_fixture

from app.database import transaction
from app.models import AuditEvent, Incident, Request, User, WorkItem, now

owner_client = owner_fixture


@pytest.mark.parametrize(
    "scenario", ["completed", "waiting", "missing_edge", "cycle", "wrong_subject", "awaiting_confirmation"]
)
def test_corrected_request_recovery(owner_client, scenario):
    with transaction() as db:
        user = User(
            email=f"recovery-{uuid4()}@example.test", display_name="Recovery Test", password_hash="Unused"
        )
        db.add(user)
        db.flush()
        request = Request(
            user_id=user.id,
            kind="Nutrition",
            status="AwaitingConfirmation" if scenario == "awaiting_confirmation" else "Completed",
            deadline=now() + timedelta(hours=1),
        )
        db.add(request)
        db.flush()
        subject = str(uuid4())
        source = WorkItem(
            request_id=request.id,
            role="Nutrition",
            subject_kind="Conversation",
            subject_id=subject,
            status="Superseded",
            wait_reason="CandidateValidation",
            input_versions={"correction_plan_id": str(uuid4())},
        )
        replacement = WorkItem(
            request_id=request.id,
            role="Nutrition",
            subject_kind="Conversation",
            subject_id=str(uuid4()) if scenario == "wrong_subject" else subject,
            status="Waiting" if scenario == "waiting" else "Completed",
            input_versions={"correction_plan_id": str(uuid4())},
        )
        db.add_all([source, replacement])
        db.flush()
        if scenario != "missing_edge":
            target = source.id if scenario == "cycle" else replacement.id
            db.add(
                AuditEvent(
                    actor_id="system",
                    request_id=request.id,
                    event_type="candidate.correction_queued",
                    subject_id=target,
                    details={"previous_work_id": source.id, "correction_work_id": target},
                )
            )
        incident = Incident(
            event_key=str(uuid4()),
            request_id=request.id,
            title="Candidate recovery",
            evidence={"work_id": source.id},
        )
        db.add(incident)
        db.flush()
        identifier, source_id = incident.id, source.id
    result = owner_client.post(f"/api/owner/incidents/{identifier}/verify-recovery")
    if scenario == "completed":
        assert result.status_code == 200, result.text
        assert result.json()["outcome"] == "Recovered"
        assert result.json()["recovery"]["superseded_task_ids"] == [source_id]
    else:
        assert result.status_code == 409, result.text
        with transaction() as db:
            assert db.get(Incident, identifier).status == "Open"
            assert db.get(Incident, identifier).outcome == "Unresolved"
