"""Version-bound requests and change-scope checks for food substitutions."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.activation import plan_fingerprint
from app.admission import fingerprint
from app.database import transaction
from app.models import (
    ChatSession,
    CurrentPlans,
    IdempotencyRecord,
    Member,
    MemberGroup,
    PlanVersion,
    Request,
    User,
    WorkItem,
    now,
)
from app.policy import PolicyDenied
from app.security import principal

router = APIRouter(prefix="/api/me/food-substitutions")


class PortionSelection(BaseModel):
    meal_index: int = Field(ge=0)
    portion_index: int = Field(ge=0)


class SubstitutionRequest(BaseModel):
    session_id: str | None = None
    key: str = Field(min_length=8, max_length=128)
    nutrition_id: str
    expected_current_version: int = Field(ge=1)
    meal_index: int = Field(ge=0)
    portion_index: int = Field(ge=0)
    selections: list[PortionSelection] = Field(default_factory=list, max_length=20)
    preference: str = Field(min_length=1, max_length=2000)


def substitution_baseline(db, request, member_id):
    if request.kind != "FoodSubstitution":
        return None
    selection = request.payload["substitution"]
    current = db.get(CurrentPlans, member_id)
    baseline = db.get(PlanVersion, selection["nutrition_id"])
    if (
        not current
        or current.version != selection["expected_current_version"]
        or current.nutrition_id != selection["nutrition_id"]
        or not baseline
        or baseline.member_id != member_id
        or baseline.kind != "Nutrition"
        or baseline.training_input_id != current.training_id
        or baseline.fingerprint != plan_fingerprint(baseline)
    ):
        raise PolicyDenied("The substitution baseline changed. Select a portion from the current plan again.")
    return baseline


def check_substitution(output, baseline, selection):
    before = baseline["meals"]
    after = output.model_dump(mode="json")["meals"]
    selected = {(p["meal_index"], p["portion_index"]) for p in selection.get("selections", [])}
    if not selected:
        selected = {(selection["meal_index"], selection["portion_index"])}
    if len(before) != len(after):
        return ["A substitution must preserve the other meals."]
    for index, (old, new) in enumerate(zip(before, after, strict=True)):
        if not any(i == index for i, _ in selected):
            if old != new:
                return ["A substitution changed an unselected meal."]
        else:
            if old["name"] != new["name"] or len(old["portions"]) != len(new["portions"]):
                return ["A substitution must preserve the meal name and portion count."]
            for offset, (a, b) in enumerate(zip(old["portions"], new["portions"], strict=True)):
                if (index, offset) not in selected and a != b:
                    return ["A substitution changed an unselected portion."]
                if (index, offset) in selected and a == b:
                    return ["A selected portion was not changed."]
    return []


@router.post("", status_code=202)
def request_substitution(body: SubstitutionRequest, user: User = Depends(principal)):
    with transaction() as db:
        return save_substitution(db, body, user)


def save_substitution(db, body: SubstitutionRequest, user: User):
    if not body.preference.strip():
        raise HTTPException(422, "Describe the requested replacement.")
    member = db.scalar(select(Member).where(Member.user_id == user.id).with_for_update())
    if not member or member.status != "Accepted":
        raise HTTPException(409, "Current admission is required.")
    group = db.get(MemberGroup, member.group_id)
    if not group or any(
        group.configuration.get("capabilities", {}).get(key) is not True
        for key in ["nutrition", "substitutions"]
    ):
        raise HTTPException(409, "Food substitution is disabled for this membership group.")
    digest = fingerprint(body.model_dump(exclude={"key"}))
    prior = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.user_id == user.id,
            IdempotencyRecord.operation == "food-substitution",
            IdempotencyRecord.key == body.key,
        )
    )
    if prior:
        if prior.fingerprint != digest:
            raise HTTPException(409, "This submission key was already used for another substitution.")
        return prior.result
    selection = body.model_dump(exclude={"key", "preference", "session_id"})
    request = Request(
        user_id=user.id,
        kind="FoodSubstitution",
        deadline=now() + timedelta(hours=24),
        payload={"content": body.preference.strip(), "substitution": selection},
    )
    try:
        baseline = substitution_baseline(db, request, member.id)
        if baseline.profile_version != member.profile_version or baseline.config_version != group.version:
            raise PolicyDenied("The plan no longer matches the confirmed profile or service settings.")
        for portion in body.selections or [PortionSelection(meal_index=body.meal_index, portion_index=body.portion_index)]:
            baseline.content["meals"][portion.meal_index]["portions"][portion.portion_index]
    except (PolicyDenied, IndexError, KeyError, TypeError) as exc:
        raise HTTPException(
            409, str(exc) if isinstance(exc, PolicyDenied) else "Select an existing meal portion."
        ) from None
    session = db.get(ChatSession, body.session_id) if body.session_id else None
    if body.session_id and (not session or session.user_id != user.id or session.deleted_at):
        raise HTTPException(404, "Conversation not found.")
    if session is None:
        session = ChatSession(user_id=user.id, title="Food substitution", active_role="Nutrition")
        db.add(session)
        db.flush()
    session.active_role = "Nutrition"
    request.session_id = session.id
    db.add(request)
    db.flush()
    db.add(
        WorkItem(
            request_id=request.id,
            role="Nutrition",
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

    start(db, request, "Nutrition")
    result = {"request_id": request.id, "session_id": session.id, "status": "Queued"}
    db.add(
        IdempotencyRecord(
            user_id=user.id,
            operation="food-substitution",
            key=body.key,
            fingerprint=digest,
            result=result,
        )
    )
    return result
