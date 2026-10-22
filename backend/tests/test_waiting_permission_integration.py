"""Waiting work must recheck current authority before recovery or dispatch."""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_admission_integration import facts, submit
from test_admission_integration import member as member_fixture
from test_admission_integration import owner_client as owner_fixture

from app.bootstrap import DEFAULT_CONFIGURATION
from app.database import transaction
from app.incidents import record_incident
from app.models import Member, MemberGroup, ModelCall, Request, User, WorkItem
from app.work_queue import claim_next, wait_for

member = member_fixture
owner_client = owner_fixture


@pytest.mark.parametrize("change", ["capability", "fields", "admission", "account", "unchanged"])
def test_waiting_recovery_rechecks_permissions(member, owner_client, change):
    accepted = submit(member, facts())
    with transaction() as db:
        group = MemberGroup(name=f"Waiting-{uuid4()}", configuration=deepcopy(DEFAULT_CONFIGURATION))
        db.add(group)
        db.flush()
        db.get(Member, accepted["member_id"]).group_id = group.id
        group_id = group.id
    conversation = member.post("/api/conversations").json()
    response = member.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"key": str(uuid4()), "purpose": "Training", "content": "Prepare a training candidate."},
    )
    assert response.status_code == 202, response.text
    request_id = response.json()["request_id"]
    claim = claim_next(request_id=request_id)
    assert claim is not None
    wait_for(claim, "BudgetRequired")
    incident_id = record_incident(claim, "BudgetRequired")
    with transaction() as db:
        member_row = db.get(Member, accepted["member_id"])
        group = db.get(MemberGroup, group_id)
        config = deepcopy(group.configuration)
        if change == "capability":
            config["capabilities"]["training"] = False
            group.configuration = config
            group.version += 1
        elif change == "fields":
            config.setdefault("field_permissions", {})["Coach"] = ["age"]
            group.configuration = config
            group.version += 1
        elif change == "admission":
            member_row.status = "Suspended"
        elif change == "account":
            db.get(User, member_row.user_id).active = False
        original_deadline = db.get(Request, request_id).deadline
    response = owner_client.post(
        f"/api/owner/incidents/{incident_id}/request-recovery",
        json={"expected_fence": claim.fence, "reason": "Recheck current permissions before continuing."},
    )
    assert response.status_code == (200 if change == "unchanged" else 409), response.text
    with transaction() as db:
        request = db.get(Request, request_id)
        item = db.get(WorkItem, claim.work_id)
        assert request.deadline == original_deadline
        assert request.model_calls == 0
        assert item.attempts == 1
        assert item.status == ("Queued" if change == "unchanged" else "Waiting")
        assert (
            db.scalar(select(func.count()).select_from(ModelCall).where(ModelCall.request_id == request_id))
            == 0
        )
    if change == "unchanged":
        # A successful recovery check is not permission to skip the next claim's check.
        with transaction() as db:
            db.get(Member, accepted["member_id"]).status = "Suspended"
        assert claim_next(request_id=request_id) is None
        with transaction() as db:
            item = db.get(WorkItem, claim.work_id)
            assert item.status == "Waiting" and item.wait_reason == "PolicyReview"
            assert item.attempts == 1
    else:
        assert claim_next(request_id=request_id) is None
