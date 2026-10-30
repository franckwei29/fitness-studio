"""Explicit, version-bound member observations with idempotent submission."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.admission import fingerprint, view
from app.database import transaction
from app.models import AuditEvent, IdempotencyRecord, Member, Observation, PlanVersion, User
from app.security import principal

router = APIRouter(prefix="/api/me")


class Feedback(BaseModel):
    key: str = Field(min_length=8, max_length=128)
    plan_id: str
    kind: Literal["TrainingFeedback", "NutritionFeedback"]
    notes: str = Field(min_length=1, max_length=5000)
    completion: Literal["Completed", "Partial", "NotCompleted"]
    difficulty: Literal["TooEasy", "Manageable", "TooHard"] | None = None
    discomfort: bool


@router.post("/observations", status_code=201)
def record_feedback(body: Feedback, user: User = Depends(principal)):
    with transaction() as db:
        return save_feedback(db, body, user)


def save_feedback(db, body: Feedback, user: User):
    if not body.notes.strip():
        raise HTTPException(422, "Provide a short description of your experience.")
    member = db.scalar(select(Member).where(Member.user_id == user.id).with_for_update())
    if not member:
        raise HTTPException(409, "A member profile is required.")
    plan = db.get(PlanVersion, body.plan_id)
    if not plan or plan.member_id != member.id:
        raise HTTPException(404, "Plan not found.")
    expected = "Training" if body.kind == "TrainingFeedback" else "Nutrition"
    if plan.kind != expected:
        raise HTTPException(422, "Feedback must match the selected plan type.")
    payload = body.model_dump(exclude={"key"})
    digest = fingerprint(payload)
    prior = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.user_id == user.id,
            IdempotencyRecord.operation == "feedback",
            IdempotencyRecord.key == body.key,
        )
    )
    if prior:
        if prior.fingerprint != digest:
            raise HTTPException(409, "This feedback key was used for different content.")
        return prior.result
    row = Observation(
        member_id=member.id,
        kind=body.kind,
        content=payload | {"plan_version": plan.version, "profile_version": member.profile_version},
    )
    db.add(row)
    db.flush()
    result = {
        "id": row.id,
        "status": row.status,
        "message": "Feedback recorded. Your current plan has not been changed.",
    }
    if body.discomfort:
        result["message"] = (
            "Feedback recorded. Pause activities that cause discomfort and seek appropriate professional guidance."
        )
    db.add(
        IdempotencyRecord(
            user_id=user.id, operation="feedback", key=body.key, fingerprint=digest, result=result
        )
    )
    db.add(
        AuditEvent(
            actor_id=user.id,
            event_type="feedback.recorded",
            subject_id=row.id,
            details={"plan_id": plan.id, "plan_version": plan.version, "kind": body.kind},
        )
    )
    return result


@router.get("/observations")
def observations(user: User = Depends(principal)):
    with transaction() as db:
        member = db.scalar(select(Member).where(Member.user_id == user.id))
        if not member:
            return []
        return [
            view(row)
            for row in db.scalars(
                select(Observation)
                .where(Observation.member_id == member.id)
                .order_by(Observation.created_at.desc())
                .limit(100)
            )
        ]
