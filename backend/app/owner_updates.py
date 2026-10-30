"""Durable owner inbox derived from authoritative business records, without AI calls."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app.database import transaction
from app.models import AuditEvent, BudgetAccount, Member, OwnerUpdate, Request, User, WorkItem, now
from app.security import owner

router = APIRouter(prefix="/api/owner/updates")


def scope_label(db, scope):
    from app.owner_workspace import account_name, group_name

    if scope == "global":
        return "Whole studio"
    if scope.startswith("group:"):
        return f"{group_name(db, scope[6:])} · Shared spending cap"
    if scope.startswith("member:"):
        member = db.get(Member, scope[7:])
        return f"{account_name(db, member.user_id) if member else 'Member'} · Personal spending cap"
    return f"AI assistant · {scope.removeprefix('role:')}"


def blocking_items(db):
    from app.owner_workspace import account_name, card
    from app.policy import PolicyDenied, execution_context

    grouped = {}
    for item, request in db.execute(
        select(WorkItem, Request)
        .join(Request, WorkItem.request_id == Request.id)
        .where(WorkItem.status == "Waiting", Request.status == "Waiting", Request.deadline > now())
    ):
        if item.wait_reason == "BudgetRequired":
            try:
                context = execution_context(db, item, request)
            except PolicyDenied:
                continue
            amount = (item.wait_context or {}).get("amount_micros")
            if not amount:
                from app.models import AgentVersion
                from app.pricing import price_for
                from app.settings import get_settings

                version = (
                    db.get(AgentVersion, item.input_versions.get("agent_version"))
                    if item.input_versions.get("agent_version")
                    else None
                )
                amount = price_for(version.model if version else get_settings().default_model).reservation()
            for scope in set(context["scope_keys"]) | {"global"}:
                budget = db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == scope))
                if budget and budget.limit_micros - budget.spent_micros - budget.reserved_micros >= amount:
                    continue
                key = "budget:" + scope
                if key not in grouped:
                    grouped[key] = card(
                        scope,
                        "Spending",
                        scope_label(db, scope),
                        "Needs attention",
                        "",
                        "budgets",
                        action="budget",
                        scope_key=scope,
                        responsibility="needs_attention",
                        affected_requests=[],
                    )
                    grouped[key]["id"] = scope
                    grouped[key]["target"]["id"] = scope
                    grouped[key]["missing"] = budget is None
                grouped[key]["affected_requests"].append(request.id)
                grouped[key]["description"] = (
                    f"{len(set(grouped[key]['affected_requests']))} request(s) paused. "
                    + (
                        "No spending limit is configured."
                        if budget is None
                        else "Available funds are below the amount required to continue."
                    )
                )
        elif item.wait_reason not in {"PolicyReview", "UsageUnknown", "UsageReconciliation"}:
            key = f"wait:{request.id}"
            reason = {
                "HourlyCallLimit": "The studio model hourly allowance is full",
                "AccountDailyCallLimit": "This account’s daily allowance is full",
                "RequestCallLimit": "This request has reached its safe processing limit",
                "ModelDispatchUnavailable": "AI processing capacity is temporarily unavailable",
            }.get(item.wait_reason, "This request needs a service review")
            target = (
                "budgets" if item.wait_reason in {"HourlyCallLimit", "AccountDailyCallLimit"} else "requests"
            )
            limit_scope = None
            if item.wait_reason == "HourlyCallLimit":
                from app.model_catalog import model_info
                from app.settings import get_settings

                model = (item.wait_context or {}).get("model") or get_settings().default_model
                limit_scope = f"model:{model_info(model)['provider']}:{model}"
            elif item.wait_reason == "AccountDailyCallLimit":
                account = db.get(User, request.user_id)
                member = db.scalar(select(Member).where(Member.user_id == request.user_id))
                limit_scope = (
                    f"owner:{account.id}"
                    if account.role == "Owner"
                    else f"member:{member.id}"
                    if member
                    else "initial"
                )
            grouped[key] = card(
                request.id,
                "Capacity" if target == "budgets" else "Service",
                f"Request paused · {account_name(db, request.user_id)}",
                "Waiting",
                f"{reason}. Review the request before making changes. Eligible capacity waits are checked automatically.",
                "dashboard",
                section="trace",
                responsibility="needs_attention",
            )
            if limit_scope:
                grouped[key]["target"] = {"page": "budgets", "id": limit_scope, "section": "call-limit"}
                grouped[key]["action"] = "call-limit"
                grouped[key]["affected_requests"] = [request.id]
    for member in db.scalars(select(Member).where(Member.status == "Accepted")):
        scope = f"member:{member.id}"
        budget = db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == scope))
        if budget is None and "budget:" + scope not in grouped:
            grouped["budget:" + scope] = card(
                scope,
                "Spending",
                scope_label(db, scope),
                "Setup required",
                "This member has been accepted. Set a personal spending cap before AI planning can begin. Existing studio and group limits still apply.",
                "budgets",
                action="budget",
                scope_key=scope,
                responsibility="needs_attention",
            )
    return list(grouped.values())


def sync(db, user):
    from app.owner_workspace import workspace_items

    # Serializes concurrent polling and scheduled reconciliation for this owner.
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    items = workspace_items(db, "attention", user)
    existing = {
        r.business_key: r for r in db.scalars(select(OwnerUpdate).where(OwnerUpdate.user_id == user.id))
    }
    active = set()
    for item in items:
        key = f"{item['target']['page']}:{item['id']}:{item['target'].get('section', '')}"
        active.add(key)
        state = "Informational" if item.get("responsibility") == "waiting_for_member" else "Needs action"
        data = jsonable_encoder(item)
        row = existing.get(key)
        if row is None:
            row = OwnerUpdate(user_id=user.id, business_key=key, item=data, state=state)
            db.add(row)
        else:
            if row.state == "Resolved":
                row.read_at = None
                row.created_at = now()
            row.item, row.state, row.resolved_at = data, state, None
    for key, row in existing.items():
        if key not in active and row.state != "Resolved":
            row.state, row.resolved_at = "Resolved", now()
    db.flush()


@router.get("")
def updates(user: User = Depends(owner)):
    with transaction() as db:
        sync(db, user)
        rows = list(
            db.scalars(
                select(OwnerUpdate)
                .where(OwnerUpdate.user_id == user.id)
                .order_by(OwnerUpdate.created_at.desc())
            )
        )
        return {
            "needs_action": sum(r.state == "Needs action" for r in rows),
            "items": [
                {
                    "id": r.id,
                    "state": r.state,
                    "read_at": r.read_at,
                    "created_at": r.created_at,
                    "resolved_at": r.resolved_at,
                    "item": r.item,
                }
                for r in rows
            ],
        }


@router.post("/{identifier}/read")
def read(identifier: str, user: User = Depends(owner)):
    with transaction() as db:
        row = db.scalar(
            select(OwnerUpdate)
            .where(OwnerUpdate.id == identifier, OwnerUpdate.user_id == user.id)
            .with_for_update()
        )
        if not row:
            raise HTTPException(404, "Update not found.")
        if row.read_at is None:
            row.read_at = now()
            db.add(AuditEvent(actor_id=user.id, subject_id=row.id, event_type="owner_update.read", details={}))
        return {"read": True}


def reconcile():
    with transaction() as db:
        owners = list(db.scalars(select(User).where(User.role == "Owner", User.active.is_(True))))
        for user in owners:
            sync(db, user)
    return len(owners)
