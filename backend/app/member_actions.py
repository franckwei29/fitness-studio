"""Member-confirmed conversation actions using the existing business services."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, ValidationError
from sqlalchemy import select

from app.agent_contracts import StrictModel
from app.database import transaction
from app.member_experience import plan_summary
from app.models import ChatSession, Member, Message, Request, User
from app.security import principal

router = APIRouter(prefix="/api/conversations")


class Selection(StrictModel):
    meal_index: int = Field(ge=0)
    portion_index: int = Field(ge=0)


class ActionDetails(StrictModel):
    plan_kind: Literal["Training", "Nutrition"] | None = None
    selections: list[Selection] | None = Field(default=None, max_length=20)
    preference: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=5000)
    completion: Literal["Completed", "Partial", "NotCompleted"] | None = None
    difficulty: Literal["TooEasy", "Manageable", "TooHard"] | None = None
    discomfort: bool | None = None
    period_start: str | None = Field(default=None, max_length=10)
    period_end: str | None = Field(default=None, max_length=10)
    planned_sessions: int | None = Field(default=None, ge=1, le=100)
    completed_sessions: int | None = Field(default=None, ge=0, le=100)
    adjustment: Literal["Progression", "Maintenance", "Deload"] | None = None


class ActionChange(StrictModel):
    revision: int = Field(ge=1)
    action: Literal["edit", "confirm", "dismiss"]
    details: ActionDetails | None = None


def pending_actions(db, session_id):
    result = []
    for message in db.scalars(select(Message).where(Message.session_id == session_id).order_by(Message.created_at.desc()).limit(50)):
        for card in message.cards or []:
            if card.get("type") == "MemberAction" and card.get("state") == "Pending":
                result.append({"message_id": message.id, **card})
    return result


def proposal(db, request, kind, details):
    summary = plan_summary(db, request.user_id)
    data = details.model_dump(exclude_none=True) if details else {}
    # Each new proposal is tied to the current baseline. Only same-kind pending input is carried forward.
    prior = next((a for a in pending_actions(db, request.session_id) if a["kind"] == kind), None)
    if prior and prior["current_version"] == summary["current_version"]:
        data = prior["details"] | data
    if kind in {"Substitution", "CycleReview"}:
        data["plan_kind"] = "Nutrition" if kind == "Substitution" else "Training"
    elif len(summary["plans"]) == 1 and not data.get("plan_kind"):
        data["plan_kind"] = summary["plans"][0]["kind"]
    # A newer proposal supersedes the prior decision, never silently confirms it.
    if prior:
        old = db.get(Message, prior["message_id"])
        old.cards = [{**c, "state": "Superseded"} if c.get("type") == "MemberAction" else c for c in old.cards]
    return {"type": "MemberAction", "kind": kind, "state": "Pending", "revision": 1,
            "current_version": summary["current_version"], "plans": summary["plans"],
            "labels": summary.get("labels", {}), "details": data}


def execute(db, card, user, session_id, key):
    from app.cycle_requests import CycleRequest, save_cycle
    from app.feedback import Feedback, save_feedback
    from app.substitution import SubstitutionRequest, save_substitution

    db.scalar(select(Member).where(Member.user_id == user.id).with_for_update())
    summary = plan_summary(db, user.id)
    if summary["current_version"] != card["current_version"]:
        raise HTTPException(409, "Your current plan changed. Ask for a new preview before confirming.")
    data = card["details"]
    plan = next((p for p in summary["plans"] if p["kind"] == data.get("plan_kind")), None)
    if not plan:
        raise HTTPException(409, "Choose an available current plan before continuing.")
    if not plan["availability"]["usable"]:
        raise HTTPException(409, plan["availability"].get("reason") or "Review your profile before continuing.")
    kind = card["kind"]
    if kind in {"Substitution", "CycleReview"}:
        active = db.scalar(select(Request.id).where(
            Request.user_id == user.id,
            Request.kind.in_(["Training", "Nutrition", "Combined", "CycleReview", "FoodSubstitution"]),
            Request.status.in_(["Queued", "Running", "Waiting", "AwaitingConfirmation"]),
        ).limit(1))
        if active:
            raise HTTPException(409, "Another plan update is still open. Finish or cancel it in your conversation before starting this change.")
    if kind == "Feedback":
        if plan["kind"] == "Training" and not data.get("difficulty"):
            raise HTTPException(422, "Tell us how difficult the training felt.")
        return save_feedback(db, Feedback(key=key, plan_id=plan["id"], kind=f'{plan["kind"]}Feedback',
            notes=data.get("notes"), completion=data.get("completion"), difficulty=data.get("difficulty"),
            discomfort=data.get("discomfort")), user)
    if kind == "Substitution":
        selected = data.get("selections") or []
        if not selected or len({(s["meal_index"], s["portion_index"]) for s in selected}) != len(selected):
            raise HTTPException(422, "Choose the exact food portions to replace.")
        return save_substitution(db, SubstitutionRequest(key=key, session_id=session_id, nutrition_id=plan["id"],
            expected_current_version=summary["current_version"], **selected[0], selections=selected,
            preference=data.get("preference")), user)
    if kind == "CycleReview":
        feedback = {k: data.get(k) for k in ["period_start", "period_end", "planned_sessions", "completed_sessions", "difficulty", "discomfort", "adjustment"]}
        return save_cycle(db, CycleRequest(key=key, session_id=session_id, feedback={**feedback,
            "experience": data.get("notes"), "confirmed": True, "training_id": plan["id"],
            "expected_current_version": summary["current_version"]}), user)
    raise HTTPException(422, "This action is unavailable.")


@router.post("/{session_id}/member-actions/{message_id}")
def change_action(session_id: str, message_id: str, body: ActionChange, user: User = Depends(principal)):
    if user.role != "Member":
        raise HTTPException(403, "This action belongs to a member account.")
    with transaction() as db:
        session = db.scalar(select(ChatSession).where(ChatSession.id == session_id).with_for_update())
        if not session or session.user_id != user.id or session.deleted_at:
            raise HTTPException(404, "Conversation not found.")
        message = db.scalar(select(Message).where(Message.id == message_id, Message.session_id == session_id).with_for_update())
        card = next((c for c in message.cards or [] if c.get("type") == "MemberAction"), None) if message else None
        if not card:
            raise HTTPException(404, "This preview is unavailable.")
        if card["state"] == "Completed" and body.action == "confirm":
            return card
        if card["state"] != "Pending" or card["revision"] != body.revision:
            raise HTTPException(409, "This preview changed. Review the latest card in your conversation.")
        updated = dict(card)
        if body.action == "edit":
            if not body.details:
                raise HTTPException(422, "Provide the details to review.")
            updated["details"] = body.details.model_dump(exclude_none=True)
        elif body.action == "dismiss":
            updated["state"] = "Dismissed"
        else:
            try:
                result = execute(db, card, user, session_id, f"member-action:{message_id}")
            except ValidationError as exc:
                missing = ", ".join(dict.fromkeys((str(e["loc"][-1]).replace("_", " ") if e["loc"] else e["msg"]) for e in exc.errors()))
                raise HTTPException(422, f"Please complete or correct: {missing}.") from None
            updated.update(state="Completed", result=result)
            db.add(Message(session_id=session_id, sender=session.active_role,
                content=result.get("message") or "Your request is submitted. We’ll prepare a draft here; your current routine has not changed.",
                cards=[{"type": "MemberReceipt", "source_message_id": message.id, "kind": card["kind"]}]))
        updated["revision"] += 1
        message.cards = [updated if c.get("type") == "MemberAction" else c for c in message.cards]
        return updated


class ActionStart(StrictModel):
    kind: Literal["Feedback", "Substitution", "CycleReview"]
    details: ActionDetails | None = None


@router.post("/{session_id}/member-actions", status_code=201)
def start_action(session_id: str, body: ActionStart, user: User = Depends(principal)):
    from types import SimpleNamespace

    if user.role != "Member":
        raise HTTPException(403, "This action belongs to a member account.")
    with transaction() as db:
        session = db.scalar(select(ChatSession).where(ChatSession.id == session_id).with_for_update())
        if not session or session.user_id != user.id or session.deleted_at:
            raise HTTPException(404, "Conversation not found.")
        card = proposal(db, SimpleNamespace(user_id=user.id, session_id=session_id), body.kind, body.details)
        role = "Nutrition" if body.kind == "Substitution" or card["details"].get("plan_kind") == "Nutrition" else "Coach"
        session.active_role = role
        message = Message(session_id=session_id, sender=role,
                          content="Let’s review the details here before you confirm.", cards=[card])
        db.add(message)
        db.flush()
        return {"message_id": message.id, "card": card}
