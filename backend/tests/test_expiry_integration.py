"""Expired waits close once while model accounting and completed work survive."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.database import transaction
from app.expiry import expire_waits
from app.models import Approval, ModelCall, Request, RequestEvent, User, WorkItem, now


def test_expiry_closes_waits_without_releasing_unknown_usage():
    # MySQL DateTime columns store whole seconds; use an exact persisted boundary.
    at = now().replace(microsecond=0)
    with transaction() as db:
        user = User(
            email=f"expiry-{uuid4()}@example.test", display_name="Expiry Test", password_hash="Unused"
        )
        db.add(user)
        db.flush()
        requests = {}
        for status in ("Waiting", "AwaitingConfirmation", "Completed", "Running", "Queued"):
            request = Request(
                user_id=user.id,
                kind="Chat",
                status=status,
                deadline=at - timedelta(seconds=1) if status != "Queued" else at + timedelta(hours=1),
                result={"retained_evidence": "synthetic"},
            )
            db.add(request)
            db.flush()
            db.add(
                WorkItem(
                    request_id=request.id,
                    role="Coach",
                    subject_kind="User",
                    subject_id=user.id,
                    status="Completed" if status == "AwaitingConfirmation" else status,
                )
            )
            requests[status] = request.id
        call = ModelCall(
            request_id=requests["Waiting"],
            user_id=user.id,
            role="Coach",
            model="synthetic",
            status="UsageUnknown",
            reserved_micros=500,
            price_version="synthetic",
        )
        approval = Approval(
            subject_kind="Synthetic",
            subject_id=requests["Waiting"],
            subject_version=1,
            fingerprint="a" * 64,
            scope={},
            context={},
            expires_at=at,
        )
        db.add_all([call, approval])
        db.flush()
        call_id, approval_id = call.id, approval.id
    expire_waits(at)
    expire_waits(at)
    with transaction() as db:
        for original, identifier in requests.items():
            request = db.get(Request, identifier)
            work = db.scalar(select(WorkItem).where(WorkItem.request_id == identifier))
            events = list(db.scalars(select(RequestEvent).where(RequestEvent.request_id == identifier)))
            if original in {"Waiting", "AwaitingConfirmation"}:
                assert request.status == "Stopped"
                assert request.result["stop_reason"] == "DeadlineExpired"
                assert request.result["retained_evidence"] == "synthetic"
                assert len(events) == 1 and events[0].event_type == "expired"
                assert work.status == ("Completed" if original == "AwaitingConfirmation" else "Stopped")
            else:
                assert request.status == original
                assert events == []
        call = db.get(ModelCall, call_id)
        assert call.status == "UsageUnknown" and call.reserved_micros == 500 and call.actual_micros is None
        assert db.get(Approval, approval_id).decision == "Expired"
