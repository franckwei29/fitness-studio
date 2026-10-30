"""Durable conversations, idempotent submissions, and resumable event delivery."""

import json
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select

from app.admission import fingerprint, view
from app.database import transaction
from app.member_status import public_event, waiting_status
from app.models import (
    AuditEvent,
    ChatSession,
    CurrentPlans,
    IdempotencyRecord,
    Member,
    MemberGroup,
    Message,
    ModelCall,
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
    "HourlyCallLimit": "The selected model’s hourly allowance has been reached. Wait for capacity, then ask the owner to request recovery.",
    "AccountDailyCallLimit": "Your account has reached its daily model-call limit. Wait for capacity, then ask the owner to request recovery.",
    "PolicyReview": "The current permissions or profile need review before continuing.",
    "UsageReconciliation": "A previous model call needs usage reconciliation before continuing.",
    "TechnicalReview": "This request needs a technical review.",
    "ExecutorUnavailable": "The assigned service is not available yet.",
    "CandidateValidation": "The candidate failed validation and cannot be used. Review the recorded issues.",
}


def request_view(db, row):
    result = view(row)
    current_work = db.scalar(
        select(WorkItem)
        .where(WorkItem.request_id == row.id, WorkItem.status.in_(["Running", "Queued", "Waiting"]))
        .order_by(
            case((WorkItem.status == "Running", 0), (WorkItem.status == "Queued", 1), else_=2),
            WorkItem.created_at,
            WorkItem.id,
        )
        .limit(1)
    )
    result["active_role"] = current_work.role if current_work else None
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
            if row.kind == "CycleReview"
            else "Your candidate is ready. Review and activate it to complete this request."
        )
    if row.status == "Queued" and (row.result or {}).get("correction_work_id"):
        result["status_message"] = (
            f"Correcting a candidate that failed validation (revision {row.revisions} of 2)."
        )
    if row.status == "Stopped" and (row.result or {}).get("stop_reason") == "CorrectionLimit":
        result["status_message"] = "Correction stopped at its limit. No invalid plan was activated."
    if not waiting and (row.result or {}).get("review_message"):
        result["status_message"] = row.result["review_message"]
    if row.status == "Stopped" and (row.result or {}).get("stop_reason") == "DeadlineExpired":
        result["status_message"] = (
            "This request expired. Its history is retained; submit a new request to continue."
        )
    if row.status == "Cancelled":
        result["status_message"] = (
            "Request cancelled. Any model call already dispatched may still incur usage; its result will not be applied."
        )
    account = db.get(User, row.user_id)
    if account and account.role != "Owner":
        if waiting:
            result["status_message"] = waiting_status(waiting.wait_reason)[1]
        elif row.status == "Cancelled":
            result["status_message"] = "This update was cancelled. Your existing plans were kept."
        elif row.status == "Stopped" and not (row.result or {}).get("review_message"):
            result["status_message"] = "This update could not be completed. Your existing plans were kept."
        result.pop("max_model_calls", None)
        result["payload"] = {
            key: value
            for key, value in (row.payload or {}).items()
            if key
            in {
                "content",
                "role",
                "speaker",
                "action",
                "auto",
                "routed",
                "requested_purpose",
                "plan_baseline",
            }
        }
        result["result"] = {
            key: value
            for key, value in (row.result or {}).items()
            if key
            in {
                "status",
                "needs_input",
                "prompt",
                "pending_goal",
                "pending_reason",
                "continued",
                "message_id",
                "host_changed",
                "training_id",
                "nutrition_id",
                "pair_id",
                "correction_work_id",
                "generation_method",
                "call_id",
            }
        }
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
    from app.member_experience import activity_view

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
            "event_cursor": db.scalar(
                select(func.max(RequestEvent.sequence))
                .join(Request, Request.id == RequestEvent.request_id)
                .where(Request.session_id == identifier)
            )
            or 0,
            "messages": [view(item) for item in messages],
            "requests": [
                request_view(db, item) | {"experience": activity_view(db, item)} for item in requests
            ],
        }


class Submission(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    key: str = Field(min_length=8, max_length=128)
    purpose: Literal[
        "Auto", "Chat", "NutritionChat", "Intake", "Training", "Nutrition", "Combined", "Operations"
    ] = "Chat"
    operations_group_id: str | None = None
    operations_ai: bool = False
    incident_id: str | None = None
    action: (
        Literal[
            "ViewPlans",
            "ConsultCoach",
            "ConsultNutrition",
            "ConsultIntake",
            "KeepChat",
            "Intake",
            "ContinuePlanning",
            "AllowCombined",
        ]
        | None
    ) = None
    source_request_id: str | None = None


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
                **({"operations_ai": True} if body.operations_ai else {}),
                **(
                    {"action": body.action, "source_request_id": body.source_request_id}
                    if body.purpose == "Auto"
                    else {}
                ),
                **({"incident_id": body.incident_id} if body.incident_id else {}),
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
        if body.operations_ai and (user.role != "Owner" or body.purpose != "Operations"):
            raise HTTPException(403, "AI operations analysis requires an owner operations request.")
        if body.purpose == "Auto":
            from app.intent_router import submit_auto

            return submit_auto(db, session, body, user)
        member = db.scalar(select(Member).where(Member.user_id == user.id))
        if body.operations_group_id and user.role != "Owner":
            raise HTTPException(403, "Only owners can select operations agent configurations.")
        if body.incident_id:
            from app.models import Incident

            if user.role != "Owner" or body.purpose != "Operations":
                raise HTTPException(403, "Incident analysis requires an owner operations request.")
            if not db.get(Incident, body.incident_id):
                raise HTTPException(404, "Incident not found.")
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
        if (
            user.role == "Owner"
            and body.purpose == "Operations"
            and not body.incident_id
            and not body.operations_ai
        ):
            from app.owner_workspace import submit_owner_message

            return submit_owner_message(db, session, body, user, digest)
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
                **({"incident_id": body.incident_id} if body.incident_id else {}),
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


@router.get("/requests")
def requests(user: User = Depends(principal)):
    with transaction() as db:
        rows = db.scalars(
            select(Request)
            .where(Request.user_id == user.id)
            .order_by(Request.created_at.desc(), Request.id)
            .limit(100)
        )
        return [request_view(db, row) for row in rows]


@router.get("/requests/{identifier}")
def request_status(identifier: str, user: User = Depends(principal)):
    with transaction() as db:
        return request_view(db, owned(db, Request, identifier, user))


@router.post("/requests/{identifier}/cancel")
def cancel_request(identifier: str, user: User = Depends(principal)):
    from app.budget import cancel_reserved_in_transaction

    with transaction() as db:
        owned(db, Request, identifier, user)
        items = list(
            db.scalars(
                select(WorkItem)
                .where(WorkItem.request_id == identifier)
                .order_by(WorkItem.id)
                .with_for_update()
            )
        )
        request = db.scalar(
            select(Request)
            .where(Request.id == identifier)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if request.status == "Cancelled":
            return request_view(db, request)
        if request.status not in {"Queued", "Running", "Waiting", "AwaitingConfirmation"}:
            raise HTTPException(409, "This request has already ended and cannot be cancelled.")
        for item in items:
            if item.status in {"Queued", "Running", "Waiting"}:
                item.status = "Cancelled"
                item.wait_reason = "UserCancelled"
                item.lease_until = None
                item.fence += 1
        request.status = "Cancelled"
        request.result = (request.result or {}) | {"status": "Cancelled", "stop_reason": "UserCancelled"}
        # Dispatch acquires the same request lock, so Reserved calls cannot race
        # past cancellation. Dispatched and unknown calls keep their evidence.
        for call_id in db.scalars(
            select(ModelCall.id)
            .where(
                ModelCall.request_id == identifier,
                ModelCall.status == "Reserved",
            )
            .order_by(ModelCall.id)
        ):
            cancel_reserved_in_transaction(db, call_id)
        db.add(RequestEvent(request_id=identifier, event_type="cancelled", payload={"status": "Cancelled"}))
        db.add(
            AuditEvent(
                actor_id=user.id,
                request_id=identifier,
                subject_id=identifier,
                event_type="request.cancelled",
                details={},
            )
        )
        return request_view(db, request)


@router.get("/requests/{identifier}/events")
def events(
    identifier: str,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None),
    user: User = Depends(principal),
):
    # A bounded stream closes after replay. The client reconnects with its cursor,
    # repeating authentication and authorization on every connection.
    with transaction() as db:
        owned(db, Request, identifier, user)
        if last_event_id is not None:
            if not last_event_id.isascii() or not last_event_id.isdecimal() or len(last_event_id) > 19:
                raise HTTPException(422, "Last-Event-ID must be a nonnegative event sequence.")
            after = max(after, int(last_event_id))
        rows = list(
            db.scalars(
                select(RequestEvent)
                .where(RequestEvent.request_id == identifier, RequestEvent.sequence > after)
                .order_by(RequestEvent.sequence)
                .limit(100)
            )
        )
        chunks = [
            f"id: {row.sequence}\nevent: {row.event_type}\ndata: {json.dumps(row.payload if user.role == 'Owner' else public_event(row.payload))}\n\n"
            for row in rows
        ]
    return StreamingResponse(
        iter(["retry: 1500\n\n", *chunks, ": reconnect\n\n"]),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
