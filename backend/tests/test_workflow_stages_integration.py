"""Stage approval is distinct from activation and cannot be reused across revisions."""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from test_activation_integration import candidate as candidate_fixture

from app.activation import ActivationConflict
from app.database import transaction
from app.models import Member, PlanPair, PlanVersion, Request, User, WorkItem
from app.workflows import FlowAction, activation_gate, perform, start, training_ready, view

candidate = candidate_fixture


def seeded(db, candidate):
    request = db.get(Request, candidate["request_id"])
    actor = db.get(User, candidate["actor_id"])
    pair = db.get(PlanPair, candidate["pair_id"])
    flow = start(db, request, "Coach")
    training_ready(db, flow, request, db.get(PlanVersion, pair.training_id))
    return request, actor, pair, flow


def test_direction_and_activation_are_separate(candidate):
    with transaction() as db:
        request, actor, pair, flow = seeded(db, candidate)
        assert flow.stage == "TrainingDiscussion"
        with pytest.raises(ActivationConflict, match="discussion"):
            activation_gate(db, request)
        plan = db.get(PlanVersion, pair.training_id)
        body = FlowAction(
            action="endorse_training",
            expected_revision=flow.revision,
            operation_key=str(uuid4()),
            target_id=plan.id,
            target_fingerprint=plan.fingerprint,
        )
        perform(db, flow, request, body, actor)
        assert flow.stage == "CheckingMeals"
        assert flow.training_endorsement["id"] == plan.id
        db.flush()
        assert db.query(WorkItem).filter_by(request_id=request.id, role="Nutrition").count() == 1
        with pytest.raises(HTTPException) as exc:
            perform(db, flow, request, body.model_copy(update={"operation_key": str(uuid4())}), actor)
        assert exc.value.status_code == 409


def test_new_training_version_invalidates_approval(candidate):
    with transaction() as db:
        request, actor, pair, flow = seeded(db, candidate)
        flow.training_endorsement = {"id": pair.training_id, "fingerprint": "old"}
        flow.nutrition_endorsement = {"id": pair.nutrition_id, "fingerprint": "old"}
        training_ready(db, flow, request, db.get(PlanVersion, pair.training_id))
        assert flow.training_endorsement is None and flow.nutrition_endorsement is None
        assert flow.pair_id is None
        with pytest.raises(HTTPException):
            perform(
                db,
                flow,
                request,
                FlowAction(
                    action="endorse_training",
                    expected_revision=flow.revision,
                    operation_key=str(uuid4()),
                    target_id=pair.training_id,
                    target_fingerprint="stale",
                ),
                actor,
            )


def test_profile_change_requires_refresh_not_automatic_dispatch(candidate):
    with transaction() as db:
        request, actor, pair, flow = seeded(db, candidate)
        db.get(Member, candidate["member_id"]).profile_version = 2
        db.flush()
        result = view(db, flow)
        assert result["issue"] and "refresh_context" in result["actions"]
        assert "endorse_training" not in result["actions"]
        assert not db.query(WorkItem).filter_by(request_id=request.id, role="Nutrition").count()


@pytest.mark.parametrize("choice", ["keep_training", "keep_meal_draft"])
def test_nutrition_led_conflict_requires_concrete_direction(candidate, choice):
    from app.models import CurrentPlans
    from app.workflows import meals_ready

    with transaction() as db:
        request = db.get(Request, candidate["request_id"])
        request.kind = "Nutrition"
        request.result = {"training_impact": True}
        pair = db.get(PlanPair, candidate["pair_id"])
        current = db.get(CurrentPlans, candidate["member_id"])
        current.training_id = pair.training_id
        db.get(PlanVersion, pair.training_id).status = "Active"
        flow = start(db, request, "Nutrition")
        meals_ready(db, flow, request, db.get(PlanVersion, pair.nutrition_id), pair, True)
        assert flow.stage == "TrainingImpact"
        assert not db.query(WorkItem).filter_by(request_id=request.id).count()
        perform(
            db,
            flow,
            request,
            FlowAction(action=choice, expected_revision=flow.revision, operation_key=str(uuid4())),
            db.get(User, candidate["actor_id"]),
        )
        assert flow.stage == ("GeneratingTraining" if choice == "keep_meal_draft" else "CheckingMeals")
        if choice == "keep_meal_draft":
            assert request.payload["preferred_meal_id"] == pair.nutrition_id
            assert request.kind == "Combined"
        else:
            assert request.kind == "Nutrition"
        assert current.nutrition_id is None
