"""Conservative reuse for an unchanged maintenance cycle, with fresh validation."""

from copy import deepcopy

from pydantic import ValidationError

from app.activation import plan_fingerprint
from app.catalogue import food_catalogue
from app.dietary_policy import dietary_constraints
from app.models import CurrentPlans, PlanVersion
from app.nutrition import NutritionProposal, validate_nutrition


def retained_nutrition(db, request, authorized, training):
    if (
        request.kind != "CycleReview"
        or request.payload.get("cycle_feedback", {}).get("adjustment") != "Maintenance"
    ):
        return None
    current = db.get(CurrentPlans, authorized["member_id"])
    if not current or not current.nutrition_id or training.baseline_id != current.training_id:
        return None
    previous_training = db.get(PlanVersion, current.training_id)
    previous_nutrition = db.get(PlanVersion, current.nutrition_id)
    if not previous_training or not previous_nutrition:
        return None
    for plan in (previous_training, previous_nutrition):
        if (
            plan.member_id != authorized["member_id"]
            or plan.status != "Active"
            or plan.profile_version != authorized["versions"]["profile"]
            or plan.config_version != authorized["versions"]["group"]
            or plan.fingerprint != plan_fingerprint(plan)
        ):
            return None
    if previous_nutrition.training_input_id != previous_training.id or training.content.get(
        "sessions"
    ) != previous_training.content.get("sessions"):
        return None
    content = deepcopy(previous_nutrition.content)
    content.pop("nutrient_totals", None)
    content["training_version_id"] = training.id
    content["rationale"] = (
        "Existing meal content retained for an unchanged maintenance training cycle; current dietary constraints and the exact new training reference were revalidated."
    )
    try:
        proposal = NutritionProposal.model_validate(content)
    except ValidationError:
        return None
    allergens, tags = dietary_constraints(authorized["profile"].get("dietary_restrictions"))
    validation = validate_nutrition(proposal, food_catalogue(), training.id, allergens, tags)
    if not validation["passed"]:
        return None
    return {
        "proposal": proposal.model_dump(mode="json"),
        "source_nutrition_id": previous_nutrition.id,
        "source_fingerprint": previous_nutrition.fingerprint,
        "rule_version": "unchanged-maintenance-reuse-v1",
    }
