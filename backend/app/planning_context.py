"""Bind direct revisions to an exact active baseline and role-specific feedback."""

from sqlalchemy import and_, or_, select

from app.activation import plan_fingerprint
from app.models import CurrentPlans, Observation, PlanVersion
from app.policy import PolicyDenied


def verify_planning_baseline(db, request, member_id):
    expected = request.payload.get("plan_baseline")
    if expected is None:
        return
    current = db.scalar(
        select(CurrentPlans)
        .where(CurrentPlans.member_id == member_id)
        .execution_options(populate_existing=True)
    )
    actual = (
        {"version": current.version, "training_id": current.training_id, "nutrition_id": current.nutrition_id}
        if current
        else None
    )
    if actual != expected:
        raise PolicyDenied(
            "The active plan changed after this revision was requested. Review the latest plan and submit again."
        )


def revision_context(db, request, authorized, role):
    verify_planning_baseline(db, request, authorized["member_id"])
    current = db.get(CurrentPlans, authorized["member_id"])
    identifier = (current.training_id if role == "Coach" else current.nutrition_id) if current else None
    plan = db.get(PlanVersion, identifier) if identifier else None
    if plan and (plan.member_id != authorized["member_id"] or plan.fingerprint != plan_fingerprint(plan)):
        raise PolicyDenied("The previous plan contents no longer match their recorded version.")
    preferred = (
        db.get(PlanVersion, request.payload.get("preferred_meal_id"))
        if request.payload.get("preferred_meal_id")
        else None
    )
    if role == "Nutrition" and preferred:
        if preferred.member_id != authorized["member_id"] or preferred.fingerprint != plan_fingerprint(
            preferred
        ):
            raise PolicyDenied("The meal draft selected for retention changed.")
        plan = preferred
    kind = "TrainingFeedback" if role == "Coach" else "NutritionFeedback"
    matching_plan = and_(
        Observation.kind == kind,
        Observation.content["plan_id"].as_string() == identifier,
    )
    if role == "Coach":
        matching_plan = or_(
            matching_plan,
            and_(
                Observation.kind == "CycleFeedback",
                Observation.content["training_id"].as_string() == identifier,
            ),
        )
    feedback = (
        list(
            db.scalars(
                select(Observation)
                .where(
                    Observation.member_id == authorized["member_id"],
                    matching_plan,
                    Observation.status == "Confirmed",
                )
                .order_by(Observation.created_at.desc(), Observation.id.desc())
                .limit(20)
            )
        )
        if identifier
        else []
    )
    from app.workflows import find

    flow = find(db, request) if getattr(request, "id", None) else None
    draft_id = (flow.training_id if role == "Coach" else flow.nutrition_id) if flow else None
    draft = db.get(PlanVersion, draft_id) if draft_id else None
    return {
        "draft_to_revise": {"id": draft.id, "content": draft.content} if draft else None,
        "member_draft_feedback": [f["text"] for f in flow.feedback if f["role"] == role] if flow else [],
        "previous_plan": None
        if not plan
        else {
            "id": plan.id,
            "version": plan.version,
            "profile_version": plan.profile_version,
            "content": plan.content,
        },
        "meal_constraint": (
            {
                "instruction": "Keep the existing meals unchanged. Adapt training to this constraint; explain any unmet training goal without inventing medical energy requirements.",
                "meals": preferred.content
                if preferred
                else db.get(PlanVersion, current.nutrition_id).content,
            }
            if (request.payload.get("keep_meals") or preferred)
            and current
            and (current.nutrition_id or preferred)
            else None
        ),
        "confirmed_feedback": [{"id": row.id, "kind": row.kind, "content": row.content} for row in feedback],
        "revision_instruction": "Use the previous plan and confirmed feedback as context. Follow the current confirmed profile constraints. Explain what changes and why; do not invent completion records.",
    }
