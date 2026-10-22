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
    return {
        "previous_plan": None
        if not plan
        else {
            "id": plan.id,
            "version": plan.version,
            "profile_version": plan.profile_version,
            "content": plan.content,
        },
        "confirmed_feedback": [{"id": row.id, "kind": row.kind, "content": row.content} for row in feedback],
        "revision_instruction": "Use the previous plan and confirmed feedback as context. Follow the current confirmed profile constraints. Explain what changes and why; do not invent completion records.",
    }
