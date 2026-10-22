"""Owner-only operational summaries drawn from persistent service records."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func, select

from app.admission import view
from app.database import transaction
from app.models import Approval, AuditEvent, BudgetAccount, Member, ModelCall, Request, User, WorkItem, now
from app.operation_summary import markdown_summary, reporting_period, summarize
from app.security import owner

router = APIRouter(prefix="/api/owner")


@router.get("/dashboard")
def dashboard(user: User = Depends(owner), start: date | None = None, end: date | None = None):
    try:
        period = reporting_period(start, end)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    with transaction() as db:
        member_counts = dict(db.execute(select(Member.status, func.count()).group_by(Member.status)).all())
        request_counts = dict(db.execute(select(Request.status, func.count()).group_by(Request.status)).all())
        pending = db.scalar(
            select(func.count())
            .select_from(Approval)
            .where(Approval.decision == "Pending", Approval.expires_at > now())
        )
        uncertain = db.scalar(
            select(func.count()).select_from(ModelCall).where(ModelCall.status == "UsageUnknown")
        )
        budgets = list(db.scalars(select(BudgetAccount).order_by(BudgetAccount.scope_key)))
        recent = list(db.scalars(select(Request).order_by(Request.created_at.desc()).limit(30)))
        return {
            "period": summarize(db, *period),
            "members": member_counts,
            "requests": request_counts,
            "pending_approvals": pending,
            "unreconciled_calls": uncertain,
            "budgets": [
                {
                    "id": row.id,
                    "scope": row.scope_key,
                    "currency": row.currency,
                    "limit_micros": row.limit_micros,
                    "spent_micros": row.spent_micros,
                    "reserved_micros": row.reserved_micros,
                    "available_micros": row.limit_micros - row.spent_micros - row.reserved_micros,
                }
                for row in budgets
            ],
            "recent_requests": [
                {
                    "id": row.id,
                    "kind": row.kind,
                    "status": row.status,
                    "created_at": row.created_at,
                    "model_calls": row.model_calls,
                }
                for row in recent
            ],
        }


@router.get("/operations-brief")
def operations_brief(user: User = Depends(owner), start: date | None = None, end: date | None = None):
    try:
        period = reporting_period(start, end)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    with transaction() as db:
        summary = summarize(db, *period)
    filename = f"operations-{summary['start']}-{summary['end']}.md"
    return Response(
        markdown_summary(summary),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/requests/{identifier}/trace")
def trace(identifier: str, user: User = Depends(owner)):
    with transaction() as db:
        request = db.get(Request, identifier)
        if not request:
            raise HTTPException(404, "Request not found.")
        tasks = list(
            db.scalars(
                select(WorkItem).where(WorkItem.request_id == identifier).order_by(WorkItem.created_at)
            )
        )
        calls = list(
            db.scalars(
                select(ModelCall).where(ModelCall.request_id == identifier).order_by(ModelCall.created_at)
            )
        )
        audits = list(
            db.scalars(
                select(AuditEvent).where(AuditEvent.request_id == identifier).order_by(AuditEvent.created_at)
            )
        )
        # Health profiles, prompts, chat text, and generated output are omitted.
        return {
            "request": {
                "id": request.id,
                "kind": request.kind,
                "status": request.status,
                "deadline": request.deadline,
                "model_calls": request.model_calls,
            },
            "tasks": [
                {
                    "id": row.id,
                    "role": row.role,
                    "status": row.status,
                    "wait_reason": row.wait_reason,
                    "attempts": row.attempts,
                    "lease_until": row.lease_until,
                    "input_versions": row.input_versions,
                }
                for row in tasks
            ],
            "calls": [
                {
                    "id": row.id,
                    "model": row.model,
                    "role": row.role,
                    "status": row.status,
                    "reserved_micros": row.reserved_micros,
                    "actual_micros": row.actual_micros,
                    "price_version": row.price_version,
                    "usage": row.usage,
                    "provider_id": row.provider_id,
                    "created_at": row.created_at,
                }
                for row in calls
            ],
            "events": [view(row) for row in audits],
        }
