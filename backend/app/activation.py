"""Exact-pair activation with atomic current pointers and idempotent receipts."""

from sqlalchemy import select

from app.admission import fingerprint
from app.database import transaction
from app.models import (
    Activation,
    Approval,
    AuditEvent,
    CurrentPlans,
    Member,
    MemberGroup,
    PlanPair,
    PlanVersion,
    Request,
    RequestEvent,
    User,
    ValidationResult,
    now,
)

PAIR_RULE_VERSION = "plan-pair-v1"


class ActivationConflict(Exception):
    """The candidate is stale, unauthorized, or no longer valid."""


def verify_cycle_activation(db, request, member_id):
    from app.cycle_requests import cycle_baseline
    from app.planning_context import verify_planning_baseline
    from app.policy import PolicyDenied
    from app.substitution import substitution_baseline

    try:
        verify_planning_baseline(db, request, member_id)
        substitution_baseline(db, request, member_id)
        if request.kind == "CycleReview":
            cycle_baseline(db, request, member_id)
    except PolicyDenied as exc:
        raise ActivationConflict(str(exc)) from None


def complete_cycle(request, result):
    if request.kind == "CycleReview" or request.payload.get("training_first"):
        request.status = "Completed"
        request.result = (request.result or {}) | {"status": "Completed", "activation": result}


def plan_fingerprint(plan):
    return fingerprint(
        {
            "member_id": plan.member_id,
            "kind": plan.kind,
            "version": plan.version,
            "profile_version": plan.profile_version,
            "config_version": plan.config_version,
            "agent_version_id": plan.agent_version_id,
            "baseline_id": plan.baseline_id,
            "training_input_id": plan.training_input_id,
            "content": plan.content,
            "rationale": plan.rationale,
        }
    )


def pair_fingerprint(training, nutrition):
    return fingerprint(
        {
            "training_id": training.id,
            "training": plan_fingerprint(training),
            "nutrition_id": nutrition.id,
            "nutrition": plan_fingerprint(nutrition),
        }
    )


def activate_pair(member_id, pair_id, request_id, actor_id, expected_current_version, operation_key):
    if not operation_key or len(operation_key) > 100:
        raise ValueError("An operation key of at most 100 characters is required.")
    with transaction() as db:
        member = db.scalar(select(Member).where(Member.id == member_id).with_for_update())
        actor = db.get(User, actor_id)
        if not member or not actor or not actor.active or member.user_id != actor.id:
            raise ActivationConflict("The member must confirm their own plan pair.")
        key = fingerprint({"member": member_id, "operation": operation_key})
        previous = db.scalar(select(Activation).where(Activation.operation_key == key))
        if previous:
            if previous.result["pair_id"] != pair_id or previous.request_id != request_id:
                raise ActivationConflict("The activation key was used for a different candidate.")
            return previous.result
        if member.status != "Accepted":
            raise ActivationConflict("Current admission is required.")
        request = db.scalar(select(Request).where(Request.id == request_id).with_for_update())
        if (
            not request
            or request.user_id != member.user_id
            or request.deadline <= now()
            or request.status in {"Cancelled", "Stopped"}
        ):
            raise ActivationConflict("The request is unavailable or expired.")
        group = db.scalar(select(MemberGroup).where(MemberGroup.id == member.group_id).with_for_update())
        capabilities = group.configuration.get("capabilities", {}) if group else {}
        if any(capabilities.get(kind) is not True for kind in ("training", "nutrition")):
            raise ActivationConflict("Training and nutrition must both be enabled.")
        current = db.scalar(select(CurrentPlans).where(CurrentPlans.member_id == member_id).with_for_update())
        if not current or current.version != expected_current_version:
            raise ActivationConflict("The current plans changed. Review the latest pair.")
        verify_cycle_activation(db, request, member_id)
        if request.payload.get("training_first"):
            from app.planning_context import verify_planning_baseline

            verify_planning_baseline(db, request, member_id)
        pair = db.scalar(select(PlanPair).where(PlanPair.id == pair_id).with_for_update())
        if not pair or pair.member_id != member_id or pair.status != "Candidate":
            raise ActivationConflict("The candidate pair is unavailable.")
        plans = {
            row.id: row
            for row in db.scalars(
                select(PlanVersion)
                .where(PlanVersion.id.in_([pair.training_id, pair.nutrition_id]))
                .order_by(PlanVersion.id)
                .with_for_update()
            )
        }
        training, nutrition = plans.get(pair.training_id), plans.get(pair.nutrition_id)
        if not training or not nutrition or training.kind != "Training" or nutrition.kind != "Nutrition":
            raise ActivationConflict("The candidate pair has invalid plan types.")
        if nutrition.request_id != request.id:
            raise ActivationConflict("The candidate pair belongs to a different request.")
        for plan in (training, nutrition):
            if (
                plan.member_id != member_id
                or plan.profile_version != member.profile_version
                or plan.config_version != group.version
                or plan.status not in {"Validated", "Active"}
                or plan.fingerprint != plan_fingerprint(plan)
            ):
                raise ActivationConflict("A plan is stale or has changed since validation.")
        if nutrition.training_input_id != training.id:
            raise ActivationConflict("Nutrition was not prepared for this exact training version.")
        validation = db.get(ValidationResult, pair.validation_id)
        digest = pair_fingerprint(training, nutrition)
        if (
            not validation
            or not validation.passed
            or validation.violations
            or validation.rule_version != PAIR_RULE_VERSION
            or validation.subject_fingerprint != digest
        ):
            raise ActivationConflict("Current validation of this exact plan pair is required.")
        if group.configuration.get("review_required"):
            approval = db.scalar(
                select(Approval).where(
                    Approval.subject_kind == "PlanPair",
                    Approval.subject_id == pair.id,
                    Approval.fingerprint == digest,
                    Approval.decision == "Approved",
                    Approval.expires_at > now(),
                )
            )
            if not approval:
                raise ActivationConflict("An exact current approval is required.")
        current.training_id, current.nutrition_id, current.pair_id = training.id, nutrition.id, pair.id
        current.version += 1
        pair.status = "Active"
        training.status = nutrition.status = "Active"
        result = {
            "pair_id": pair.id,
            "training_id": training.id,
            "nutrition_id": nutrition.id,
            "current_version": current.version,
            "status": "Committed",
        }
        db.add(Activation(operation_key=key, member_id=member_id, request_id=request.id, result=result))
        complete_cycle(request, result)
        db.add(
            AuditEvent(
                actor_id=actor_id,
                request_id=request.id,
                event_type="plans.activated",
                subject_id=pair.id,
                details=result,
            )
        )
        return result


def activate_training(member_id, plan_id, actor_id, expected_current_version, operation_key):
    if not operation_key or len(operation_key) > 100:
        raise ValueError("An operation key of at most 100 characters is required.")
    with transaction() as db:
        member = db.scalar(select(Member).where(Member.id == member_id).with_for_update())
        actor = db.get(User, actor_id)
        if not member or not actor or not actor.active or member.user_id != actor.id:
            raise ActivationConflict("The member must confirm their own training plan.")
        key = fingerprint({"member": member_id, "operation": operation_key, "kind": "Training"})
        previous = db.scalar(select(Activation).where(Activation.operation_key == key))
        if previous:
            if previous.result.get("training_id") != plan_id:
                raise ActivationConflict("The activation key was used for a different plan.")
            return previous.result
        plan = db.scalar(select(PlanVersion).where(PlanVersion.id == plan_id).with_for_update())
        if not plan or plan.member_id != member_id or plan.kind != "Training":
            raise ActivationConflict("Training candidate not found.")
        request = db.scalar(select(Request).where(Request.id == plan.request_id).with_for_update())
        if (
            not request
            or request.kind not in {"Training", "CycleReview"}
            or request.deadline <= now()
            or request.status in {"Cancelled", "Stopped"}
        ):
            raise ActivationConflict("A current standalone training request is required.")
        group = db.scalar(select(MemberGroup).where(MemberGroup.id == member.group_id).with_for_update())
        current = db.scalar(select(CurrentPlans).where(CurrentPlans.member_id == member_id).with_for_update())
        if not current or current.version != expected_current_version:
            raise ActivationConflict("The current plan changed. Review the latest version.")
        verify_cycle_activation(db, request, member_id)
        if request.kind == "CycleReview" and request.payload.get("coordinate_nutrition"):
            raise ActivationConflict("This cycle review requires a compatible nutrition pair.")
        nutrition_enabled = group and group.configuration.get("capabilities", {}).get("nutrition") is True
        if current.nutrition_id and nutrition_enabled:
            raise ActivationConflict("The existing nutrition plan requires an exact compatible pair.")
        if (
            member.status != "Accepted"
            or group.configuration.get("capabilities", {}).get("training") is not True
            or plan.profile_version != member.profile_version
            or plan.config_version != group.version
            or plan.status != "Validated"
            or plan.fingerprint != plan_fingerprint(plan)
        ):
            raise ActivationConflict("The candidate or its current service context is invalid.")
        from app.training import TRAINING_RULE_VERSION

        validation = db.scalar(
            select(ValidationResult).where(
                ValidationResult.subject_fingerprint == plan.fingerprint,
                ValidationResult.rule_version == TRAINING_RULE_VERSION,
                ValidationResult.passed.is_(True),
            )
        )
        if not validation or validation.violations:
            raise ActivationConflict("Current training validation is required.")
        if group.configuration.get("review_required"):
            approval = db.scalar(
                select(Approval).where(
                    Approval.subject_kind == "TrainingPlan",
                    Approval.subject_id == plan.id,
                    Approval.fingerprint == plan.fingerprint,
                    Approval.decision == "Approved",
                    Approval.expires_at > now(),
                )
            )
            if not approval:
                raise ActivationConflict("A current training approval is required.")
        current.training_id = plan.id
        current.nutrition_id = None
        current.pair_id = None
        current.version += 1
        plan.status = "Active"
        result = {"training_id": plan.id, "current_version": current.version, "status": "Committed"}
        db.add(Activation(operation_key=key, member_id=member.id, request_id=request.id, result=result))
        complete_cycle(request, result)
        db.add(
            AuditEvent(
                actor_id=actor.id,
                request_id=request.id,
                event_type="training.activated",
                subject_id=plan.id,
                details=result,
            )
        )
        return result


def activate_nutrition(member_id, plan_id, actor_id, expected_current_version, operation_key):
    """Activate an independent nutrition candidate without inventing a plan pair."""
    from app.nutrition import NUTRITION_RULE_VERSION

    if not operation_key or len(operation_key) > 100:
        raise ValueError("An operation key of at most 100 characters is required.")
    with transaction() as db:
        member = db.scalar(select(Member).where(Member.id == member_id).with_for_update())
        actor = db.get(User, actor_id)
        if not member or not actor or not actor.active or member.user_id != actor.id:
            raise ActivationConflict("The member must confirm their own nutrition plan.")
        key = fingerprint({"member": member_id, "operation": operation_key, "kind": "Nutrition"})
        previous = db.scalar(select(Activation).where(Activation.operation_key == key))
        if previous:
            if previous.result.get("nutrition_id") != plan_id:
                raise ActivationConflict("The activation key was used for a different plan.")
            return previous.result
        plan = db.scalar(select(PlanVersion).where(PlanVersion.id == plan_id).with_for_update())
        if not plan or plan.member_id != member_id or plan.kind != "Nutrition":
            raise ActivationConflict("Nutrition candidate not found.")
        request = db.scalar(select(Request).where(Request.id == plan.request_id).with_for_update())
        if (
            not request
            or request.user_id != member.user_id
            or request.kind not in {"Nutrition", "FoodSubstitution"}
            or request.deadline <= now()
            or request.status != "AwaitingConfirmation"
        ):
            raise ActivationConflict(
                "A current standalone nutrition request awaiting confirmation is required."
            )
        group = db.scalar(select(MemberGroup).where(MemberGroup.id == member.group_id).with_for_update())
        current = db.scalar(select(CurrentPlans).where(CurrentPlans.member_id == member_id).with_for_update())
        if not current or current.version != expected_current_version:
            raise ActivationConflict("The current plans changed. Review the latest version.")
        verify_cycle_activation(db, request, member_id)
        if (
            current.training_id
            and group
            and group.configuration.get("capabilities", {}).get("training") is True
        ):
            raise ActivationConflict("The current training plan requires an exact compatible pair.")
        if (
            not group
            or member.status != "Accepted"
            or group.configuration.get("capabilities", {}).get("nutrition") is not True
            or plan.profile_version != member.profile_version
            or plan.config_version != group.version
            or plan.status != "Validated"
            or plan.fingerprint != plan_fingerprint(plan)
            or plan.training_input_id is not None
            or plan.content.get("training_version_id") is not None
        ):
            raise ActivationConflict("The nutrition candidate or its current service context is invalid.")
        validation = db.scalar(
            select(ValidationResult).where(
                ValidationResult.subject_fingerprint == plan.fingerprint,
                ValidationResult.rule_version == NUTRITION_RULE_VERSION,
                ValidationResult.passed.is_(True),
            )
        )
        if not validation or validation.violations:
            raise ActivationConflict("Current nutrition validation is required.")
        if group.configuration.get("review_required"):
            approval = db.scalar(
                select(Approval).where(
                    Approval.subject_kind == "NutritionPlan",
                    Approval.subject_id == plan.id,
                    Approval.fingerprint == plan.fingerprint,
                    Approval.decision == "Approved",
                    Approval.expires_at > now(),
                )
            )
            if not approval:
                raise ActivationConflict("A current nutrition approval is required.")
        current.nutrition_id = plan.id
        current.training_id = current.pair_id = None
        current.version += 1
        plan.status = "Active"
        result = {"nutrition_id": plan.id, "current_version": current.version, "status": "Committed"}
        db.add(Activation(operation_key=key, member_id=member.id, request_id=request.id, result=result))
        request.status = "Completed"
        request.result = (request.result or {}) | {"status": "Completed", "activation": result}
        db.add(
            RequestEvent(
                request_id=request.id,
                event_type="completed",
                payload={"status": "Completed", "nutrition_id": plan.id},
            )
        )
        db.add(
            AuditEvent(
                actor_id=actor.id,
                request_id=request.id,
                event_type="nutrition.activated",
                subject_id=plan.id,
                details=result,
            )
        )
        return result
