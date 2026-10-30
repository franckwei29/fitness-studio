"""Persist confirmed cycle evidence and bind review to the active baseline."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.activation import plan_fingerprint
from app.admission import fingerprint
from app.cycle_review import CycleFeedback
from app.database import transaction
from app.models import (
    ChatSession,
    CurrentPlans,
    IdempotencyRecord,
    Member,
    MemberGroup,
    Observation,
    PlanVersion,
    Request,
    User,
    WorkItem,
    now,
)
from app.policy import PolicyDenied
from app.security import principal

router = APIRouter(prefix="/api/me/cycle-reviews")


class CycleRequest(BaseModel):
    session_id: str | None = None
    key: str = Field(min_length=8, max_length=128)
    feedback: CycleFeedback


def cycle_baseline(db, request, member_id):
    if request.kind != "CycleReview":
        return None
    feedback = CycleFeedback.model_validate(request.payload["cycle_feedback"])
    if request.id:
        observation = (
            db.get(Observation, request.payload.get("observation_id"))
            if request.payload.get("observation_id")
            else None
        )
        if (
            not observation
            or observation.member_id != member_id
            or observation.request_id != request.id
            or observation.kind != "CycleFeedback"
            or observation.status != "Confirmed"
            or observation.content != request.payload["cycle_feedback"]
        ):
            raise PolicyDenied("The confirmed cycle feedback is missing or changed. Submit a new review.")
    current = db.get(CurrentPlans, member_id)
    baseline = db.get(PlanVersion, feedback.training_id)
    if (
        not current
        or current.version != feedback.expected_current_version
        or current.training_id != feedback.training_id
        or not baseline
        or baseline.member_id != member_id
        or baseline.kind != "Training"
        or baseline.fingerprint != plan_fingerprint(baseline)
    ):
        raise PolicyDenied("The reviewed training baseline changed. Review the current cycle again.")
    return baseline


@router.post("", status_code=202)
def request_cycle(body: CycleRequest, user: User = Depends(principal)):
    with transaction() as db:
        return save_cycle(db, body, user)


def save_cycle(db, body: CycleRequest, user: User):
    member = db.scalar(select(Member).where(Member.user_id == user.id).with_for_update())
    if not member or member.status != "Accepted":
        raise HTTPException(409, "Current admission is required for cycle review.")
    group = db.get(MemberGroup, member.group_id)
    if not group or group.configuration.get("capabilities", {}).get("training") is not True:
        raise HTTPException(409, "Training is disabled for this membership group.")
    feedback = body.feedback.model_dump(mode="json")
    digest = fingerprint(feedback | ({"session_id": body.session_id} if body.session_id else {}))
    previous = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.user_id == user.id,
            IdempotencyRecord.operation == "cycle-review",
            IdempotencyRecord.key == body.key,
        )
    )
    if previous:
        if previous.fingerprint != digest:
            raise HTTPException(409, "This submission key belongs to different feedback.")
        return previous.result
    request = Request(
        user_id=user.id,
        kind="CycleReview",
        deadline=now() + timedelta(hours=24),
        payload={
            "content": "Review the member-confirmed cycle feedback and propose the next cycle.",
            "cycle_feedback": feedback,
            "coordinate_nutrition": group.configuration.get("capabilities", {}).get("nutrition") is True,
        },
    )
    try:
        baseline = cycle_baseline(db, request, member.id)
        if baseline.profile_version != member.profile_version or baseline.config_version != group.version:
            raise PolicyDenied(
                "Review the updated profile and service configuration before planning another cycle."
            )
    except PolicyDenied as exc:
        raise HTTPException(409, str(exc)) from None
    session = db.get(ChatSession, body.session_id) if body.session_id else None
    if body.session_id and (not session or session.user_id != user.id or session.deleted_at):
        raise HTTPException(404, "Conversation not found.")
    if session is None:
        session = ChatSession(user_id=user.id, title="Cycle review", active_role="Coach")
        db.add(session)
        db.flush()
    session.active_role = "Coach"
    request.session_id = session.id
    db.add(request)
    db.flush()
    observation = Observation(
        member_id=member.id, request_id=request.id, kind="CycleFeedback", content=feedback
    )
    db.add(observation)
    db.flush()
    request.payload = request.payload | {"observation_id": observation.id}
    db.add(
        WorkItem(
            request_id=request.id,
            role="Coach",
            subject_kind="Conversation",
            subject_id=session.id,
            input_versions={
                "profile": member.profile_version,
                "group": group.version,
                "group_id": group.id,
            },
        )
    )
    from app.workflows import start

    start(db, request, "Coach")
    result = {
        "request_id": request.id,
        "session_id": session.id,
        "observation_id": observation.id,
        "status": "Queued",
    }
    db.add(
        IdempotencyRecord(
            user_id=user.id, operation="cycle-review", key=body.key, fingerprint=digest, result=result
        )
    )
    return result
