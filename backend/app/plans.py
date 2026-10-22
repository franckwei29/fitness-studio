"""Member-scoped plan history and explicit pair confirmation endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.activation import ActivationConflict, activate_pair
from app.admission import view
from app.catalogue import exercise_catalogue, food_catalogue
from app.database import transaction
from app.models import CurrentPlans, Member, MemberGroup, PlanPair, PlanVersion, User
from app.security import principal

router = APIRouter(prefix="/api")


def availability(member, group, plan):
    """Explain whether a saved plan still matches the current service context."""
    if member.status != "Accepted":
        return {"usable": False, "reason": "Your admission needs review before using this plan."}
    if not group or plan.profile_version != member.profile_version or plan.config_version != group.version:
        return {
            "usable": False,
            "reason": "Your profile or service settings changed. Request an updated plan.",
        }
    capability = "training" if plan.kind == "Training" else "nutrition"
    if group.configuration.get("capabilities", {}).get(capability) is not True:
        return {"usable": False, "reason": "This service is currently disabled for your membership group."}
    return {"usable": True, "reason": None}


@router.get("/me/plans")
def my_plans(user: User = Depends(principal)):
    with transaction() as db:
        member = db.scalar(select(Member).where(Member.user_id == user.id))
        if not member:
            return {"member_status": None, "current": None, "plans": [], "pairs": []}
        current = db.get(CurrentPlans, member.id)
        group = db.get(MemberGroup, member.group_id)
        plans = list(
            db.scalars(
                select(PlanVersion)
                .where(PlanVersion.member_id == member.id)
                .order_by(PlanVersion.created_at.desc(), PlanVersion.id)
            )
        )
        pairs = list(
            db.scalars(
                select(PlanPair)
                .where(PlanPair.member_id == member.id)
                .order_by(PlanPair.created_at.desc(), PlanPair.id)
            )
        )
        return {
            "member_status": member.status,
            "catalogue_labels": {
                row.id: row.name for row in [*food_catalogue().values(), *exercise_catalogue().values()]
            },
            "substitutions_enabled": bool(
                group and group.configuration.get("capabilities", {}).get("substitutions") is True
            ),
            "current": view(current) if current else None,
            "plans": [view(plan) | {"availability": availability(member, group, plan)} for plan in plans],
            "pairs": [view(pair) for pair in pairs],
        }


class PairConfirmation(BaseModel):
    request_id: str
    expected_current_version: int = Field(ge=0)
    operation_key: str = Field(min_length=8, max_length=100)


@router.post("/me/plans/pairs/{identifier}/confirm")
def confirm_pair(identifier: str, body: PairConfirmation, user: User = Depends(principal)):
    with transaction() as db:
        member = db.scalar(select(Member).where(Member.user_id == user.id))
        pair = db.get(PlanPair, identifier)
        if not member or not pair or pair.member_id != member.id:
            raise HTTPException(404, "Plan pair not found.")
        member_id = member.id
    try:
        return activate_pair(
            member_id, identifier, body.request_id, user.id, body.expected_current_version, body.operation_key
        )
    except ActivationConflict as exc:
        raise HTTPException(409, str(exc)) from None


class TrainingConfirmation(BaseModel):
    expected_current_version: int = Field(ge=0)
    operation_key: str = Field(min_length=8, max_length=100)


@router.post("/me/plans/training/{identifier}/confirm")
def confirm_training(identifier: str, body: TrainingConfirmation, user: User = Depends(principal)):
    from app.activation import activate_training

    with transaction() as db:
        member = db.scalar(select(Member).where(Member.user_id == user.id))
        plan = db.get(PlanVersion, identifier)
        if not member or not plan or plan.member_id != member.id:
            raise HTTPException(404, "Training plan not found.")
        member_id = member.id
    try:
        return activate_training(
            member_id, identifier, user.id, body.expected_current_version, body.operation_key
        )
    except ActivationConflict as exc:
        raise HTTPException(409, str(exc)) from None
