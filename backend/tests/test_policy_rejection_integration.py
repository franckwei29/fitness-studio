"""Policy failures leave a durable incident and redacted task audit at both gates."""

import pytest
from sqlalchemy import select
from test_work_queue_integration import task as task_fixture

from app.database import transaction
from app.models import AuditEvent, Incident, ModelCall, WorkItem
from app.policy import PolicyDenied
from app.worker import run_once

task = task_fixture


@pytest.mark.parametrize("stage", ["claim", "execution"])
def test_policy_rejection_is_visible_without_provider_dispatch(monkeypatch, task, stage):
    request_id, work_id, _ = task

    def denied(*args):
        raise PolicyDenied("PRIVATE-CONTEXT-MUST-NOT-BE-IN-AUDIT")

    if stage == "claim":
        monkeypatch.setattr("app.work_queue.execution_context", denied)
    else:
        monkeypatch.setattr("app.worker.run_intake", denied)
    assert run_once(request_id) is (stage == "execution")
    assert not run_once(request_id)
    with transaction() as db:
        work = db.get(WorkItem, work_id)
        assert work.status == "Waiting" and work.wait_reason == "PolicyReview"
        incidents = list(db.scalars(select(Incident).where(Incident.request_id == request_id)))
        audits = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.request_id == request_id, AuditEvent.event_type == "task.policy_rejected"
                )
            )
        )
        assert len(incidents) == len(audits) == 1
        assert incidents[0].evidence["work_id"] == work_id
        assert audits[0].details == {"role": "Intake", "stage": stage, "reason": "PolicyReview"}
        assert "PRIVATE" not in str(incidents[0].evidence)
        assert list(db.scalars(select(ModelCall).where(ModelCall.request_id == request_id))) == []
