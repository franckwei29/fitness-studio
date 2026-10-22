"""Correlated incident evidence and explicit unresolved closure."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.admission import fingerprint, view
from app.database import transaction
from app.models import AuditEvent, Incident, Request, User, WorkItem, now
from app.security import owner

router = APIRouter(prefix="/api/owner/incidents")


def record_incident(claim, reason):
    key = fingerprint({"work_id": claim.work_id, "reason": reason})
    with transaction() as db:
        item = db.scalar(select(WorkItem).where(WorkItem.id == claim.work_id).with_for_update())
        if not item:
            return
        previous = db.scalar(select(Incident).where(Incident.event_key == key))
        if previous:
            return previous.id
        incident = Incident(
            event_key=key,
            request_id=item.request_id,
            title=f"{item.role} request needs attention",
            evidence={
                "work_id": item.id,
                "role": item.role,
                "reason": reason,
                "attempts": item.attempts,
                "fence": item.fence,
            },
        )
        db.add(incident)
        db.flush()
        return incident.id


@router.get("")
def incidents(user: User = Depends(owner)):
    with transaction() as db:
        result = []
        for row in db.scalars(select(Incident).order_by(Incident.created_at.desc()).limit(100)):
            item = db.get(WorkItem, row.evidence.get("work_id"))
            result.append(
                view(row)
                | {
                    "current_fence": item.fence if item else None,
                    "task_status": item.status if item else None,
                }
            )
        return result


class Closure(BaseModel):
    reason: str = Field(min_length=1, max_length=3000)


@router.post("/{identifier}/close-unresolved")
def close_unresolved(identifier: str, body: Closure, user: User = Depends(owner)):
    with transaction() as db:
        incident = db.scalar(select(Incident).where(Incident.id == identifier).with_for_update())
        if not incident:
            raise HTTPException(404, "Incident not found.")
        if incident.status == "Closed":
            return view(incident)
        incident.status = "Closed"
        incident.outcome = "Unresolved"
        incident.recovery = {
            "closure_reason": body.reason,
            "closed_at": now().isoformat(),
            "service_restored": False,
        }
        db.add(
            AuditEvent(
                actor_id=user.id,
                request_id=incident.request_id,
                event_type="incident.closed_unresolved",
                subject_id=incident.id,
                details={"reason": body.reason},
            )
        )
        return view(incident)


def completed_task_chain(db, request, tasks):
    """Every replaced task must have a recorded same-subject path to completion."""
    if not tasks:
        return False
    by_id = {task.id: task for task in tasks}
    replacements = {}
    for event in db.scalars(
        select(AuditEvent).where(
            AuditEvent.request_id == request.id,
            AuditEvent.event_type == "candidate.correction_queued",
        )
    ):
        source = event.details.get("previous_work_id")
        target = event.details.get("correction_work_id")
        if event.subject_id != target or source in replacements:
            return False
        replacements[source] = target
    for task in tasks:
        visited = set()
        while task.status == "Superseded":
            if task.id in visited or task.wait_reason != "CandidateValidation":
                return False
            visited.add(task.id)
            successor = by_id.get(replacements.get(task.id))
            if not successor or (successor.role, successor.subject_kind, successor.subject_id) != (
                task.role,
                task.subject_kind,
                task.subject_id,
            ):
                return False
            if not successor.input_versions.get("correction_plan_id"):
                return False
            task = successor
        if task.status != "Completed":
            return False
    return True


@router.post("/{identifier}/verify-recovery")
def verify_recovery(identifier: str, user: User = Depends(owner)):
    with transaction() as db:
        incident = db.scalar(select(Incident).where(Incident.id == identifier).with_for_update())
        if not incident:
            raise HTTPException(404, "Incident not found.")
        if incident.evidence.get("release_id"):
            from app.rollback_recovery import verify_release_service

            return verify_release_service(db, incident, user.id)
        request = db.get(Request, incident.request_id) if incident.request_id else None
        tasks = list(db.scalars(select(WorkItem).where(WorkItem.request_id == incident.request_id)))
        if not request or request.status != "Completed" or not completed_task_chain(db, request, tasks):
            raise HTTPException(409, "The affected request has not completed successfully.")
        incident.status = "Closed"
        incident.outcome = "Recovered"
        incident.recovery = {
            "verified_at": now().isoformat(),
            "request_id": request.id,
            "task_ids": [row.id for row in tasks],
            "superseded_task_ids": [row.id for row in tasks if row.status == "Superseded"],
            "service_restored": True,
        }
        db.add(
            AuditEvent(
                actor_id=user.id,
                request_id=request.id,
                event_type="incident.recovery_verified",
                subject_id=incident.id,
                details=incident.recovery,
            )
        )
        return view(incident)


class RecoveryRequest(BaseModel):
    expected_fence: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=3000)


@router.post("/{identifier}/request-recovery")
def request_recovery(identifier: str, body: RecoveryRequest, user: User = Depends(owner)):
    from app.models import ModelCall
    from app.policy import PolicyDenied, execution_context
    from app.work_queue import event

    with transaction() as db:
        incident = db.scalar(select(Incident).where(Incident.id == identifier).with_for_update())
        if not incident or incident.status == "Closed":
            raise HTTPException(409, "An open incident is required.")
        item = db.scalar(
            select(WorkItem).where(WorkItem.id == incident.evidence.get("work_id")).with_for_update()
        )
        if not item or item.status != "Waiting" or item.fence != body.expected_fence:
            raise HTTPException(409, "The task state has changed. Refresh the incident.")
        request = db.scalar(select(Request).where(Request.id == item.request_id).with_for_update())
        if request.deadline <= now() or item.attempts >= 3 or request.model_calls >= request.max_model_calls:
            raise HTTPException(409, "The original request has reached its deadline or attempt limit.")
        unsettled = db.scalar(
            select(ModelCall.id)
            .where(
                ModelCall.request_id == request.id,
                ModelCall.status.in_(["Reserved", "Dispatching", "UsageUnknown"]),
            )
            .limit(1)
        )
        if unsettled:
            raise HTTPException(409, "Reconcile outstanding model calls before requesting recovery.")
        if item.wait_reason in {"CandidateValidation", "ExecutorUnavailable"}:
            raise HTTPException(
                409, "This task requires a revised candidate or available executor before recovery."
            )
        try:
            execution_context(db, item, request)
        except PolicyDenied as exc:
            raise HTTPException(409, str(exc)) from None
        item.status = request.status = "Queued"
        item.wait_reason = None
        item.fence += 1
        incident.status = "RecoveryRequested"
        incident.recovery = {
            "requested_by": user.id,
            "requested_at": now().isoformat(),
            "reason": body.reason,
            "service_restored": False,
            "fence": item.fence,
        }
        db.add(
            AuditEvent(
                actor_id=user.id,
                request_id=request.id,
                event_type="incident.recovery_requested",
                subject_id=incident.id,
                details=incident.recovery,
            )
        )
        event(db, request, "recovery_queued", status="Queued", role=item.role)
        return view(incident)
