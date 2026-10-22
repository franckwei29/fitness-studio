"""Owner-only operational summaries drawn from persistent service records."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func, select

from app.database import transaction
from app.models import Approval, BudgetAccount, Member, ModelCall, Request, User, now
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
    from app.settings import get_settings
    from app.trace_data import request_trace

    settings = get_settings()
    return request_trace(identifier) | {
        "external_export_configured": bool(
            settings.studio_langsmith_export_enabled and settings.studio_langsmith_api_key.get_secret_value()
        )
    }
