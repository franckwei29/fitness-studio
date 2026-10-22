"""Durable conversations, idempotent submissions, and resumable event delivery."""

import json
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.admission import fingerprint, view
from app.database import transaction
from app.models import (
    ChatSession,
    CurrentPlans,
    IdempotencyRecord,
    Member,
    MemberGroup,
    Message,
    Request,
    RequestEvent,
    User,
    WorkItem,
    now,
)
from app.security import principal

router = APIRouter(prefix="/api")


def owned(db, model, identifier, user, lock=False):
    query = select(model).where(model.id == identifier, model.user_id == user.id)
    row = db.scalar(query.with_for_update() if lock else query)
    if not row:
        raise HTTPException(404, "Record not found.")
    return row


WAIT_MESSAGES = {
    "BudgetRequired": "Waiting for model budget to be configured or replenished.",
    "ModelDispatchUnavailable": "Waiting for model access or an available dispatch slot.",
    "PolicyReview": "The current permissions or profile need review before continuing.",
    "UsageReconciliation": "A previous model call needs usage reconciliation before continuing.",
    "TechnicalReview": "This request needs a technical review.",
    "ExecutorUnavailable": "The assigned service is not available yet.",
    "CandidateValidation": "The candidate failed validation and cannot be used. Review the recorded issues.",
}


def request_view(db, row):
    result = view(row)
    waiting = db.scalar(
        select(WorkItem)
        .where(WorkItem.request_id == row.id, WorkItem.status == "Waiting")
        .order_by(WorkItem.created_at)
        .limit(1)
    )
    result["status_message"] = (
        WAIT_MESSAGES.get(waiting.wait_reason, "Your request is waiting for the next step.")
        if waiting
        else None
    )
    if row.status == "AwaitingConfirmation":
        result["status_message"] = (
            "Your cycle candidate is ready. Review and activate the new plan to complete the review."
        )
    if row.status == "Queued" and (row.result or {}).get("correction_work_id"):
        result["status_message"] = f"Correcting a candidate that failed validation (revision {row.revisions} of 2)."
    if row.status == "Stopped" and (row.result or {}).get("stop_reason") == "CorrectionLimit":
        result["status_message"] = "Correction stopped at its limit. No invalid plan was activated."
    if row.status == "Stopped" and (row.result or {}).get("stop_reason") == "DeadlineExpired":
        result["status_message"] = "This request expired. Its history is retained; submit a new request to continue."
    return result


@router.post("/conversations", status_code=201)
def create_conversation(user: User = Depends(principal)):
    with transaction() as db:
        row = ChatSession(
            user_id=user.id, active_role="TechnicalManager" if user.role == "Owner" else "Intake"
        )
        db.add(row)
        db.flush()
        return view(row)


@router.get("/conversations")
def conversations(user: User = Depends(principal)):
    with transaction() as db:
        return [
            view(row)
            for row in db.scalars(
                select(ChatSession)
                .where(ChatSession.user_id == user.id)
                .order_by(ChatSession.created_at.desc())
            )
        ]


@router.get("/conversations/{identifier}")
def conversation(identifier: str, user: User = Depends(principal)):
    with transaction() as db:
        row = owned(db, ChatSession, identifier, user)
        messages = db.scalars(
            select(Message).where(Message.session_id == identifier).order_by(Message.created_at, Message.id)
        )
        requests = db.scalars(
            select(Request).where(Request.session_id == identifier).order_by(Request.created_at, Request.id)
        )
        return {
            "conversation": view(row),
            "messages": [view(item) for item in messages],
            "requests": [request_view(db, item) for item in requests],
        }


class Submission(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    key: str = Field(min_length=8, max_length=128)
    purpose: Literal["Chat", "NutritionChat", "Intake", "Training", "Nutrition", "Combined", "Operations"] = (
        "Chat"
    )
    operations_group_id: str | None = None


@router.post("/conversations/{identifier}/messages", status_code=202)
def submit_message(identifier: str, body: Submission, user: User = Depends(principal)):
    if not body.content.strip():
        raise HTTPException(422, "Enter a message before sending.")
    with transaction() as db:
        session = owned(db, ChatSession, identifier, user, lock=True)
        operation = f"chat:{identifier}"
        digest = fingerprint(
            {
                "content": body.content,
                "purpose": body.purpose,
                "operations_group_id": body.operations_group_id,
            }
        )
        previous = db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user_id == user.id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.key == body.key,
            )
        )
        if previous:
            if previous.fingerprint != digest:
                raise HTTPException(409, "This submission key was used for different content.")
            return previous.result
        member = db.scalar(select(Member).where(Member.user_id == user.id))
        if body.operations_group_id and user.role != "Owner":
            raise HTTPException(403, "Only owners can select operations agent configurations.")
        operations_group = None
        if user.role == "Owner":
            operations_group = (
                db.get(MemberGroup, body.operations_group_id)
                if body.operations_group_id
                else db.scalar(select(MemberGroup).where(MemberGroup.name == "Default"))
            )
            if not operations_group:
                raise HTTPException(404, "Operations agent configuration not found.")
        if body.purpose == "Intake" and user.role != "Member":
            raise HTTPException(403, "Intake conversations require a member account.")
        if body.purpose == "Operations" and user.role != "Owner":
            raise HTTPException(403, "Owner access is required.")
        if body.purpose in {"Training", "Nutrition", "Combined"} and (
            not member or member.status != "Accepted"
        ):
            raise HTTPException(409, "Complete admission before requesting a plan.")
        role = (
            "TechnicalManager"
            if user.role == "Owner"
            else (
                "Intake"
                if body.purpose == "Intake" or not member or member.status != "Accepted"
                else "Nutrition"
                if body.purpose in {"Nutrition", "NutritionChat"}
                else "Coach"
            )
        )
        message = Message(session_id=session.id, sender="User", content=body.content)
        kind = "Chat" if body.purpose == "NutritionChat" else body.purpose
        baseline = None
        if member and body.purpose in {"Training", "Nutrition", "Combined"}:
            current = db.get(CurrentPlans, member.id)
            if current:
                baseline = {
                    "version": current.version,
                    "training_id": current.training_id,
                    "nutrition_id": current.nutrition_id,
                }
                if body.purpose == "Training" and current.nutrition_id:
                    group = db.get(MemberGroup, member.group_id)
                    if group and group.configuration.get("capabilities", {}).get("nutrition") is True:
                        kind = "Combined"
        request = Request(
            user_id=user.id,
            session_id=session.id,
            kind=kind,
            deadline=now() + timedelta(hours=24),
            payload={
                "content": body.content,
                "role": role,
                "operations_group_id": operations_group.id if operations_group else None,
                "requested_purpose": body.purpose,
                **({"plan_baseline": baseline} if baseline is not None else {}),
            },
        )
        db.add_all([message, request])
        db.flush()
        db.add(
            WorkItem(
                request_id=request.id,
                role=role,
                subject_kind="Conversation",
                subject_id=session.id,
                input_versions={
                    "profile": member.profile_version if member else None,
                    "group_id": operations_group.id
                    if operations_group
                    else member.group_id
                    if member
                    else None,
                    "group": operations_group.version if operations_group else None,
                },
            )
        )
        db.add(
            RequestEvent(
                request_id=request.id, event_type="queued", payload={"status": "Queued", "role": role}
            )
        )
        session.active_role = role
        result = {"request_id": request.id, "message_id": message.id, "status": "Queued"}
        db.add(
            IdempotencyRecord(
                user_id=user.id, operation=operation, key=body.key, fingerprint=digest, result=result
            )
        )
        return result


@router.get("/requests/{identifier}")
def request_status(identifier: str, user: User = Depends(principal)):
    with transaction() as db:
        return request_view(db, owned(db, Request, identifier, user))


@router.get("/requests/{identifier}/events")
def events(identifier: str, after: int = Query(default=0, ge=0), user: User = Depends(principal)):
    # A bounded stream closes after replay. The client reconnects with its cursor,
    # repeating authentication and authorization on every connection.
    with transaction() as db:
        owned(db, Request, identifier, user)
        rows = list(
            db.scalars(
                select(RequestEvent)
                .where(RequestEvent.request_id == identifier, RequestEvent.sequence > after)
                .order_by(RequestEvent.sequence)
                .limit(100)
            )
        )
        chunks = [
            f"id: {row.sequence}\nevent: {row.event_type}\ndata: {json.dumps(row.payload)}\n\n"
            for row in rows
        ]
    return StreamingResponse(
        iter(["retry: 1500\n\n", *chunks, ": reconnect\n\n"]),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
