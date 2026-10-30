"""Unknown-usage oversight without inventing charges or refunding held funds."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy import select

from app.database import transaction
from app.models import AuditEvent, ModelCall, User, WorkItem, now
from app.rate_limit import ModelRateLimiter
from app.security import owner
from app.settings import get_settings

router = APIRouter(prefix="/api/owner/unknown-usage")


def release_allowed(db, call):
    if call.status != "UsageUnknown" or call.created_at > now() - timedelta(minutes=5):
        return False
    return (
        db.scalar(
            select(WorkItem.id)
            .where(WorkItem.request_id == call.request_id, WorkItem.status == "Running")
            .limit(1)
        )
        is None
    )


@router.get("")
def unknown_usage(user: User = Depends(owner)):
    with Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2) as connection:
        active = set(ModelRateLimiter(connection).active_calls())
    with transaction() as db:
        calls = list(
            db.scalars(
                select(ModelCall).where(ModelCall.status == "UsageUnknown").order_by(ModelCall.created_at)
            )
        )
        return [
            {
                "id": call.id,
                "request_id": call.request_id,
                "role": call.role,
                "model": call.model,
                "reserved_micros": call.reserved_micros,
                "created_at": call.created_at,
                "local_slot_held": call.id in active,
                "can_release_local_slot": release_allowed(db, call),
            }
            for call in calls
        ]


class ReleaseSlot(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)


@router.post("/{identifier}/release-slot")
def release_local_slot(identifier: str, body: ReleaseSlot, user: User = Depends(owner)):
    if len(body.reason.strip()) < 10:
        raise HTTPException(422, "Explain why the local execution slot can be released.")
    with transaction() as db:
        call = db.scalar(select(ModelCall).where(ModelCall.id == identifier).with_for_update())
        if not call:
            raise HTTPException(404, "Model call not found.")
        if not release_allowed(db, call):
            raise HTTPException(
                409,
                "Only unknown calls older than five minutes with no running local task can release their slot.",
            )
        # No budget accounts or provider usage fields are modified. Remote billing
        # remains uncertain even after the local dispatch slot is released.
        with Redis.from_url(
            get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2
        ) as connection:
            ModelRateLimiter(connection).release(call.id)
        db.add(
            AuditEvent(
                actor_id=user.id,
                request_id=call.request_id,
                event_type="model.local_slot_released",
                subject_id=call.id,
                details={
                    "reason": body.reason,
                    "billing_status": "UsageUnknown",
                    "reservation_retained_micros": call.reserved_micros,
                },
            )
        )
        return {
            "status": "LocalSlotReleased",
            "billing_status": "UsageUnknown",
            "reservation_retained_micros": call.reserved_micros,
        }


@router.post("/{identifier}/acknowledge")
def acknowledge_usage(identifier: str, user: User = Depends(owner)):
    """Acknowledge an uncertain charge without changing accounting or recovery."""
    with transaction() as db:
        call = db.scalar(select(ModelCall).where(ModelCall.id == identifier).with_for_update())
        if not call or call.status != "UsageUnknown":
            raise HTTPException(409, "This usage record has changed. Refresh before continuing.")
        existing = db.scalar(
            select(AuditEvent.id)
            .where(
                AuditEvent.subject_id == call.id,
                AuditEvent.event_type == "model.usage_acknowledged",
            )
            .limit(1)
        )
        if not existing:
            db.add(
                AuditEvent(
                    actor_id=user.id,
                    request_id=call.request_id,
                    subject_id=call.id,
                    event_type="model.usage_acknowledged",
                    details={
                        "reservation_retained_micros": call.reserved_micros,
                        "billing_status": "UsageUnknown",
                    },
                )
            )
        return {"status": "Monitoring", "reservation_retained_micros": call.reserved_micros}
