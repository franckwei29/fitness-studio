"""Atomic pair activation and stale-version rejection using synthetic plans."""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.activation import (
    PAIR_RULE_VERSION,
    ActivationConflict,
    activate_pair,
    pair_fingerprint,
    plan_fingerprint,
)
from app.database import transaction
from app.models import (
    CurrentPlans,
    Member,
    MemberGroup,
    PlanPair,
    PlanVersion,
    Request,
    User,
    ValidationResult,
    now,
)


@pytest.fixture
def candidate():
    with transaction() as db:
        user = User(
            email=f"activation-{uuid4()}@example.test", display_name="Activation Test", password_hash="Unused"
        )
        group = MemberGroup(
            name=f"Test-{uuid4()}", configuration={"capabilities": {"training": True, "nutrition": True}}
        )
        db.add_all([user, group])
        db.flush()
        member = Member(user_id=user.id, group_id=group.id)
        request = Request(user_id=user.id, kind="Combined", deadline=now() + timedelta(hours=1))
        db.add_all([member, request])
        db.flush()
        db.add(CurrentPlans(member_id=member.id))
        plans = []
        for kind in ["Training", "Nutrition"]:
            plan = PlanVersion(
                member_id=member.id,
                request_id=request.id,
                kind=kind,
                version=1,
                status="Validated",
                profile_version=1,
                config_version=1,
                agent_version_id="synthetic",
                content={"test": True},
                rationale="Synthetic test",
                fingerprint="pending",
                training_input_id=plans[0].id if plans else None,
            )
            db.add(plan)
            db.flush()
            plan.fingerprint = plan_fingerprint(plan)
            plans.append(plan)
        validation = ValidationResult(
            subject_fingerprint=pair_fingerprint(*plans),
            rule_version=PAIR_RULE_VERSION,
            passed=True,
            violations=[],
            basis={"synthetic": True},
        )
        db.add(validation)
        db.flush()
        pair = PlanPair(
            member_id=member.id,
            training_id=plans[0].id,
            nutrition_id=plans[1].id,
            validation_id=validation.id,
        )
        db.add(pair)
        db.flush()
        return dict(
            member_id=member.id,
            pair_id=pair.id,
            request_id=request.id,
            actor_id=user.id,
            expected_current_version=0,
            operation_key=str(uuid4()),
        )


@pytest.mark.parametrize("status", ["Cancelled", "Stopped"])
def test_ended_request_cannot_activate_a_valid_pair(candidate, status):
    with transaction() as db:
        db.get(Request, candidate["request_id"]).status = status
    with pytest.raises(ActivationConflict, match="unavailable or expired"):
        activate_pair(**candidate)
    with transaction() as db:
        assert db.get(CurrentPlans, candidate["member_id"]).version == 0


def test_exact_pair_activates_once(candidate):
    result = activate_pair(**candidate)
    assert result["status"] == "Committed"
    assert activate_pair(**candidate) == result
    with transaction() as db:
        current = db.get(CurrentPlans, candidate["member_id"])
        assert current.version == 1
        assert current.training_id == result["training_id"]
        assert current.nutrition_id == result["nutrition_id"]


def test_changed_profile_cannot_activate_old_pair(candidate):
    with transaction() as db:
        db.get(Member, candidate["member_id"]).profile_version = 2
    with pytest.raises(ActivationConflict, match="stale"):
        activate_pair(**candidate)
    with transaction() as db:
        current = db.get(CurrentPlans, candidate["member_id"])
        assert current.version == 0
        assert current.training_id is None
        assert current.nutrition_id is None


def test_modified_content_invalidates_validation(candidate):
    with transaction() as db:
        pair = db.get(PlanPair, candidate["pair_id"])
        db.get(PlanVersion, pair.nutrition_id).content = {"modified": True}
    with pytest.raises(ActivationConflict, match="changed"):
        activate_pair(**candidate)


def test_pair_cannot_be_confirmed_under_another_request(candidate):
    with transaction() as db:
        unrelated = Request(
            user_id=candidate["actor_id"], kind="Combined", deadline=now() + timedelta(hours=1)
        )
        db.add(unrelated)
        db.flush()
        candidate["request_id"] = unrelated.id
    with pytest.raises(ActivationConflict, match="different request"):
        activate_pair(**candidate)
    with transaction() as db:
        assert db.get(CurrentPlans, candidate["member_id"]).version == 0


def test_substitution_cannot_override_a_newer_current_plan(candidate):
    with transaction() as db:
        pair = db.get(PlanPair, candidate["pair_id"])
        request = db.get(Request, candidate["request_id"])
        request.kind = "FoodSubstitution"
        request.payload = {"substitution": {"nutrition_id": pair.nutrition_id, "expected_current_version": 1}}
        current = db.get(CurrentPlans, candidate["member_id"])
        current.training_id = pair.training_id
        current.nutrition_id = pair.nutrition_id
        current.version = 2
    candidate["expected_current_version"] = 2
    with pytest.raises(ActivationConflict, match="substitution baseline changed"):
        activate_pair(**candidate)
    with transaction() as db:
        assert db.get(CurrentPlans, candidate["member_id"]).version == 2


@pytest.mark.parametrize("disabled_nutrition", [False, True])
def test_standalone_training_activation(candidate, disabled_nutrition):
    from app.activation import activate_training
    from app.training import TRAINING_RULE_VERSION

    with transaction() as db:
        pair = db.get(PlanPair, candidate["pair_id"])
        training = db.get(PlanVersion, pair.training_id)
        if disabled_nutrition:
            member = db.get(Member, candidate["member_id"])
            group = db.get(MemberGroup, member.group_id)
            group.configuration = {"capabilities": {"training": True, "nutrition": False}}
            current = db.get(CurrentPlans, member.id)
            current.nutrition_id = pair.nutrition_id
            current.pair_id = pair.id
        db.get(Request, candidate["request_id"]).kind = "Training"
        db.add(
            ValidationResult(
                subject_fingerprint=training.fingerprint,
                rule_version=TRAINING_RULE_VERSION,
                passed=True,
                violations=[],
                basis={},
            )
        )
        plan_id = training.id
    arguments = dict(
        member_id=candidate["member_id"],
        plan_id=plan_id,
        actor_id=candidate["actor_id"],
        expected_current_version=0,
        operation_key=str(uuid4()),
    )
    result = activate_training(**arguments)
    assert activate_training(**arguments) == result
    with transaction() as db:
        current = db.get(CurrentPlans, candidate["member_id"])
        assert current.training_id == plan_id
        assert current.nutrition_id is None
        assert current.pair_id is None
        if disabled_nutrition:
            assert db.get(PlanVersion, pair.nutrition_id) is not None
