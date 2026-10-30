"""Audited owner budget limits; changing a limit never dispatches a model call."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.admission import fingerprint
from app.database import transaction
from app.models import AuditEvent, BudgetAccount, Member, MemberGroup, Request, User
from app.security import owner

router = APIRouter(prefix="/api/owner/budgets")


def budget_view(row):
    data = {
        "id": row.id,
        "scope_key": row.scope_key,
        "currency": row.currency,
        "limit_micros": row.limit_micros,
        "spent_micros": row.spent_micros,
        "reserved_micros": row.reserved_micros,
    }
    return data | {"fingerprint": fingerprint(data)}


@router.get("")
def budgets(user: User = Depends(owner)):
    with transaction() as db:
        return [
            budget_view(row) for row in db.scalars(select(BudgetAccount).order_by(BudgetAccount.scope_key))
        ]


class BudgetChange(BaseModel):
    scope_key: str = Field(min_length=1, max_length=100)
    limit_micros: int = Field(ge=0, le=1000000000, strict=True)
    expected_fingerprint: str | None = None
    reason: str = Field(min_length=1, max_length=1000)


@router.post("")
def change_budget(body: BudgetChange, user: User = Depends(owner)):
    with transaction() as db:
        waiting_ids = list(db.scalars(select(Request.id).where(Request.status == "Waiting")))
        # Serialize account creation and changes without relying on a check-then-
        # insert race. Dispatch reservations lock the account row separately.
        db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == "global").with_for_update())
        scope = body.scope_key
        if scope == "global" or scope in {
            f"role:{role}" for role in ["Intake", "Coach", "Nutrition", "TechnicalManager"]
        }:
            pass
        elif scope.startswith("group:") and db.get(MemberGroup, scope[6:]):
            pass
        elif scope.startswith("member:") and db.get(Member, scope[7:]):
            pass
        else:
            raise HTTPException(422, "Choose a valid global, role, group, or member budget scope.")
        row = db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == scope).with_for_update())
        previous_limit = 0
        if row:
            if body.expected_fingerprint != budget_view(row)["fingerprint"]:
                raise HTTPException(409, "The budget changed. Refresh before editing.")
            previous_limit = row.limit_micros
        else:
            if body.expected_fingerprint is not None:
                raise HTTPException(409, "The budget account does not exist.")
            row = BudgetAccount(scope_key=scope, limit_micros=0)
            db.add(row)
            db.flush()
        row.limit_micros = body.limit_micros
        db.info["resume_waiting"] = True
        db.add(
            AuditEvent(
                actor_id=user.id,
                event_type="budget.limit_changed",
                subject_id=row.id,
                details={
                    "scope_key": scope,
                    "previous_limit_micros": previous_limit,
                    "limit_micros": body.limit_micros,
                    "reason": body.reason,
                },
            )
        )
        result = budget_view(row)
    with transaction() as db:
        pending = list(db.scalars(select(Request).where(Request.id.in_(waiting_ids)))) if waiting_ids else []
        result["recovery"] = {
            "resumed": sum(
                r.status in {"Queued", "Running", "Completed", "AwaitingConfirmation"} for r in pending
            ),
            "still_waiting": sum(r.status == "Waiting" for r in pending),
            "message": "Waiting requests were checked against all applicable limits. Open Updates for remaining blockers.",
        }
    return result
