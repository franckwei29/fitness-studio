"""Close expired waits without dispatching models or altering usage evidence."""

from sqlalchemy import exists, select

from app.database import transaction
from app.models import Approval, Request, WorkItem, now
from app.work_queue import event

WAITING_STATES = ("Queued", "Waiting", "AwaitingConfirmation")


def expire_waits(at=None, batch_size=100):
    at = at or now()
    if not 1 <= batch_size <= 1000:
        raise ValueError("Expiry batch size must be between 1 and 1000.")
    with transaction() as db:
        identifiers = list(
            db.scalars(
                select(Request.id)
                .where(
                    Request.status.in_(WAITING_STATES),
                    Request.deadline <= at,
                    ~exists(
                        select(WorkItem.id).where(
                            WorkItem.request_id == Request.id, WorkItem.status == "Running"
                        )
                    ),
                )
                .order_by(Request.deadline, Request.id)
                .limit(batch_size)
            )
        )
    expired = 0
    for identifier in identifiers:
        with transaction() as db:
            # Workers lock work before its parent request. Preserve that order.
            items = list(
                db.scalars(
                    select(WorkItem)
                    .where(WorkItem.request_id == identifier)
                    .order_by(WorkItem.id)
                    .with_for_update()
                )
            )
            request = db.scalar(select(Request).where(Request.id == identifier).with_for_update())
            if request.status not in WAITING_STATES or request.deadline > at:
                continue
            # Lease recovery handles running work and uncertain provider outcomes.
            if any(item.status == "Running" for item in items):
                continue
            for item in items:
                if item.status in WAITING_STATES:
                    item.status = "Stopped"
                    item.wait_reason = "DeadlineExpired"
                    item.lease_until = None
                    item.fence += 1
            request.status = "Stopped"
            request.result = (request.result or {}) | {
                "status": "Stopped",
                "stop_reason": "DeadlineExpired",
            }
            event(db, request, "expired", status="Stopped", reason="DeadlineExpired")
            expired += 1
    with transaction() as db:
        approvals = list(
            db.scalars(
                select(Approval)
                .where(Approval.decision == "Pending", Approval.expires_at <= at)
                .order_by(Approval.expires_at, Approval.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        for approval in approvals:
            approval.decision = "Expired"
    return expired
