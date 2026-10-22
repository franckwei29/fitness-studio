"""Database-backed claims with fencing and conservative crash recovery."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select

from app.database import transaction
from app.models import AuditEvent, ModelCall, Request, RequestEvent, User, WorkItem, now
from app.policy import PolicyDenied, execution_context


@dataclass(frozen=True)
class Claim:
    work_id: str
    request_id: str
    role: str
    fence: int


class StaleClaim(Exception):
    """The worker no longer owns an unexpired claim."""


def event(db, request, kind, **payload):
    db.add(RequestEvent(request_id=request.id, event_type=kind, payload=payload))


def record_policy_rejection(db, item, request, stage):
    from app.incidents import record_incident_in_transaction

    db.add(
        AuditEvent(
            actor_id="system",
            request_id=request.id,
            subject_id=item.id,
            event_type="task.policy_rejected",
            details={"role": item.role, "stage": stage, "reason": "PolicyReview"},
        )
    )
    record_incident_in_transaction(db, item, "PolicyReview")


def claim_next(lease_seconds=120, request_id=None):
    if lease_seconds < 1:
        raise ValueError("Lease duration must be positive.")
    with transaction() as db:
        query = select(WorkItem).where(WorkItem.status == "Queued")
        if request_id:
            query = query.where(WorkItem.request_id == request_id)
        item = db.scalar(
            query.order_by(WorkItem.created_at, WorkItem.id).limit(1).with_for_update(skip_locked=True)
        )
        if item is None:
            return None
        request = db.scalar(select(Request).where(Request.id == item.request_id).with_for_update())
        if request.status in {"Cancelled", "Stopped", "Completed"}:
            item.status = "Stopped"
            item.wait_reason = "RequestEnded"
            return None
        user = db.get(User, request.user_id)
        if request.deadline <= now() or item.attempts >= 3 or not user or not user.active:
            item.status = request.status = "Stopped"
            item.wait_reason = "DeadlineOrAttemptLimit" if user and user.active else "AccountUnavailable"
            event(db, request, "stopped", status="Stopped", reason=item.wait_reason)
            return None
        try:
            context = execution_context(db, item, request)
        except PolicyDenied as exc:
            item.status = request.status = "Waiting"
            item.wait_reason = "PolicyReview"
            record_policy_rejection(db, item, request, "claim")
            event(db, request, "waiting", status="Waiting", reason=str(exc))
            return None
        item.input_versions = item.input_versions | context["versions"]
        item.fence += 1
        item.attempts += 1
        item.status = request.status = "Running"
        item.lease_until = now() + timedelta(seconds=lease_seconds)
        event(db, request, "processing", status="Running", role=item.role)
        return Claim(item.id, request.id, item.role, item.fence)


def lock_claim(db, claim):
    item = db.scalar(select(WorkItem).where(WorkItem.id == claim.work_id).with_for_update())
    if (
        not item
        or item.fence != claim.fence
        or item.status != "Running"
        or not item.lease_until
        or item.lease_until <= now()
    ):
        raise StaleClaim("The task claim has expired or been superseded.")
    request = db.scalar(select(Request).where(Request.id == item.request_id).with_for_update())
    if request.deadline <= now() or request.status in {"Cancelled", "Stopped", "Completed"}:
        raise StaleClaim("The request has ended or its deadline has expired.")
    return item, request


def heartbeat(claim, lease_seconds=120):
    if lease_seconds < 1:
        raise ValueError("Lease duration must be positive.")
    with transaction() as db:
        item, request = lock_claim(db, claim)
        item.lease_until = min(request.deadline, now() + timedelta(seconds=lease_seconds))


def wait_for(claim, reason):
    with transaction() as db:
        item, request = lock_claim(db, claim)
        item.status = request.status = "Waiting"
        item.wait_reason = reason
        item.lease_until = None
        if reason == "PolicyReview":
            record_policy_rejection(db, item, request, "execution")
        event(db, request, "waiting", status="Waiting", reason=reason, role=item.role)


def recover_expired():
    """Never automatically redispatch a potentially billable interrupted call."""
    recovered = 0
    with transaction() as db:
        items = list(
            db.scalars(
                select(WorkItem)
                .where(WorkItem.status == "Running", WorkItem.lease_until <= now())
                .order_by(WorkItem.id)
                .limit(50)
                .with_for_update(skip_locked=True)
            )
        )
        for item in items:
            request = db.scalar(select(Request).where(Request.id == item.request_id).with_for_update())
            calls = list(
                db.scalars(
                    select(ModelCall).where(
                        ModelCall.request_id == request.id,
                        ModelCall.status.in_(["Reserved", "Dispatching", "UsageUnknown"]),
                    )
                )
            )
            for call in calls:
                if call.status == "Dispatching":
                    call.status = "UsageUnknown"
            if calls:
                status, reason = "Waiting", "UsageReconciliation"
            elif request.deadline <= now() or item.attempts >= 3:
                status, reason = "Stopped", "DeadlineOrAttemptLimit"
            else:
                status, reason = "Queued", None
            item.fence += 1
            item.status = request.status = status
            item.wait_reason = reason
            item.lease_until = None
            event(db, request, "recovery", status=status, reason=reason, role=item.role)
            recovered += 1
    return recovered
