"""Owner decisions gate each supported plan subject before member activation."""

from datetime import timedelta

import pytest
from test_activation_integration import candidate as candidate_fixture
from test_admission_integration import owner_client as owner_fixture

from app.activation import (
    ActivationConflict,
    activate_nutrition,
    activate_pair,
    activate_training,
    pair_fingerprint,
    plan_fingerprint,
)
from app.database import transaction
from app.models import (
    Approval,
    CurrentPlans,
    Member,
    MemberGroup,
    PlanPair,
    PlanVersion,
    Request,
    ValidationResult,
    now,
)
from app.nutrition import NUTRITION_RULE_VERSION
from app.training import TRAINING_RULE_VERSION

candidate = candidate_fixture
owner_client = owner_fixture


@pytest.mark.parametrize("kind", ["Training", "Nutrition", "PlanPair"])
@pytest.mark.parametrize("decision", ["Approved", "Revise", "Rejected", "Expired"])
def test_plan_decision_matrix(candidate, owner_client, kind, decision):
    with transaction() as db:
        member = db.get(Member, candidate["member_id"])
        group = db.get(MemberGroup, member.group_id)
        group.configuration = group.configuration | {"review_required": True}
        pair = db.get(PlanPair, candidate["pair_id"])
        training = db.get(PlanVersion, pair.training_id)
        nutrition = db.get(PlanVersion, pair.nutrition_id)
        request = db.get(Request, candidate["request_id"])
        if kind == "PlanPair":
            subject_id = pair.id
            digest = pair_fingerprint(training, nutrition)
        else:
            request.kind = kind
            request.status = "AwaitingConfirmation"
            plan = training if kind == "Training" else nutrition
            if kind == "Nutrition":
                plan.training_input_id = None
                plan.content = {"title": "Independent nutrition", "training_version_id": None}
                plan.fingerprint = plan_fingerprint(plan)
            subject_id, digest = plan.id, plan.fingerprint
            db.add(
                ValidationResult(
                    subject_fingerprint=digest,
                    rule_version=TRAINING_RULE_VERSION if kind == "Training" else NUTRITION_RULE_VERSION,
                    passed=True,
                    violations=[],
                    basis={"synthetic": True},
                )
            )
        request.result = {"pair_id" if kind == "PlanPair" else kind.lower() + "_id": subject_id}
        approval = Approval(
            subject_kind=kind if kind == "PlanPair" else kind + "Plan",
            subject_id=subject_id,
            subject_version=1,
            fingerprint=digest,
            scope={"member_id": member.id},
            context={"synthetic": True},
            expires_at=now() + timedelta(hours=1),
        )
        db.add(approval)
        db.flush()
        approval_id = approval.id

    def activate():
        if kind == "PlanPair":
            return activate_pair(**candidate)
        executor = activate_training if kind == "Training" else activate_nutrition
        return executor(
            candidate["member_id"], subject_id, candidate["actor_id"], 0, candidate["operation_key"]
        )

    with pytest.raises(ActivationConflict):
        activate()
    endpoint = f"/api/owner/approvals/{approval_id}/decisions"
    body = {"decision": decision, "fingerprint": digest, "comment": "Synthetic decision matrix"}
    if decision == "Expired":
        with transaction() as db:
            db.get(Approval, approval_id).expires_at = now() - timedelta(seconds=2)
        response = owner_client.post(endpoint, json=body | {"decision": "Approved"})
        assert response.status_code == 409
        rows = owner_client.get("/api/owner/approvals").json()
        assert next(row for row in rows if row["id"] == approval_id)["decision"] == "Expired"
    else:
        response = owner_client.post(endpoint, json=body)
        assert response.status_code == 200, response.text
        assert owner_client.post(endpoint, json=body).json() == response.json()
        assert (
            owner_client.post(
                endpoint, json=body | {"decision": "Rejected" if decision == "Approved" else "Approved"}
            ).status_code
            == 409
        )
    if decision == "Approved":
        result = activate()
        assert result["status"] == "Committed"
        assert activate() == result
    else:
        with pytest.raises(ActivationConflict):
            activate()
    with transaction() as db:
        current = db.get(CurrentPlans, candidate["member_id"])
        assert current.version == int(decision == "Approved")
        if decision != "Approved":
            assert current.training_id is None and current.nutrition_id is None
        assert db.get(Approval, approval_id).decision == decision

    if decision == "Revise":
        # The matrix checks admission to revision; lifecycle tests execute it.
        with transaction() as db:
            from sqlalchemy import select
            from app.models import WorkItem

            request = db.get(Request, candidate["request_id"])
            assert request.revisions == 1 and request.status == "Queued"
            for task in db.scalars(select(WorkItem).where(WorkItem.request_id == request.id)):
                task.status = "Cancelled"
            request.status = "Cancelled"
