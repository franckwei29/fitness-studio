"""Member-facing, read-only projections. Internal workflow states stay internal."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select

from app.database import transaction
from app.member_status import waiting_status
from app.models import (
    Approval,
    BudgetAccount,
    CurrentPlans,
    Member,
    MemberGroup,
    PlanPair,
    PlanVersion,
    Request,
    User,
    WorkItem,
    now,
)
from app.plans import availability
from app.rate_limit import ModelRateLimiter
from app.security import principal
from app.settings import get_settings

router = APIRouter(prefix="/api/me")


def plan_summary(db, user_id):
    from app.activation import plan_fingerprint
    from app.catalogue import exercise_catalogue, food_catalogue

    member = db.scalar(select(Member).where(Member.user_id == user_id))
    current = db.get(CurrentPlans, member.id) if member else None
    group = db.get(MemberGroup, member.group_id) if member else None
    result = {
        "current_version": current.version if current else 0,
        "plans": [],
        "schedule_note": "These are plan sessions, not appointments on a calendar.",
    }
    for kind, identifier in [
        ("Training", current.training_id if current else None),
        ("Nutrition", current.nutrition_id if current else None),
    ]:
        plan = db.get(PlanVersion, identifier) if identifier else None
        if not plan or plan.member_id != member.id or plan.kind != kind:
            continue
        usable = availability(member, group, plan)
        if plan.fingerprint != plan_fingerprint(plan):
            usable = {"usable": False, "reason": "This plan needs a studio review."}
        result["plans"].append(
            {
                "id": plan.id,
                "kind": kind,
                "version": plan.version,
                "content": plan.content if usable["usable"] else {},
                "availability": usable,
            }
        )
    result["labels"] = {p.id: p.name for p in [*food_catalogue().values(), *exercise_catalogue().values()]}
    return result


def activity_view(db, request):
    member = db.scalar(select(Member).where(Member.user_id == request.user_id))
    current = db.get(CurrentPlans, member.id) if member else None
    plans = list(db.scalars(select(PlanVersion).where(PlanVersion.request_id == request.id)))
    works = list(
        db.scalars(select(WorkItem).where(WorkItem.request_id == request.id).order_by(WorkItem.created_at))
    )
    active = next((w for w in works if w.status == "Running"), None) or next(
        (w for w in works if w.status in {"Queued", "Waiting"}), None
    )
    reason = next((w.wait_reason for w in works if w.status == "Waiting"), None)
    title = {
        "Auto": "Your conversation",
        "Chat": "Your conversation",
        "Intake": "Your profile review",
        "Training": "Your training update",
        "Nutrition": "Your meal update",
        "Combined": "Your training and meal update",
        "CycleReview": "Your routine review",
        "FoodSubstitution": "Your meal replacement",
        "Operations": "Studio analysis",
    }.get(request.kind, "Your update")
    if request.payload.get("training_first"):
        title = "Your training update · Meal compatibility review"
    if (request.payload or {}).get("local_lookup"):
        title = "Workspace lookup"
    status, detail, action = (
        "Waiting to start",
        "Your update will start when assistance is available.",
        "conversation",
    )
    needs = False
    if request.status == "Running":
        status = "In progress"
        detail = (
            "Understanding your request."
            if request.kind == "Auto"
            else {
                "Coach": "Coach is preparing your training.",
                "Nutrition": "Checking your existing meals against the training draft."
                if request.payload.get("training_first")
                else "Nutrition Assistant is preparing your meals.",
                "Intake": "Front Desk is reviewing your details.",
            }.get(active.role if active else "", "Preparing your answer.")
        )
    elif request.status == "Cancelled":
        status, detail = "Cancelled", "This update was cancelled. Your existing plans were kept."
    elif request.status == "Stopped":
        status, detail = (
            "Update stopped",
            "We could not finish this update. Your current plans were not replaced.",
        )
        if (request.result or {}).get("stop_reason") == "DeadlineExpired":
            status, detail = "Expired", "This update expired. Start a new conversation to continue."
    elif request.status == "Waiting":
        status, detail, _ = waiting_status(reason)
    elif request.status in {"Completed", "AwaitingConfirmation"}:
        status, detail = "Answer ready", "Your answer is available in the conversation."
        if (request.result or {}).get("needs_input"):
            status, detail, needs = (
                "Needs your input",
                (request.result or {}).get("prompt", "Continue the conversation to provide a few details."),
                True,
            )
        current_ids = {current.training_id, current.nutrition_id} if current else set()
        if plans:
            action = "plans"
            group = db.get(MemberGroup, member.group_id) if member else None
            usable = [p for p in plans if member and availability(member, group, p)["usable"]]
            if any(p.id in current_ids for p in plans):
                if any(p.id in current_ids for p in usable):
                    status, detail = "In use", "This update is included in your current routine."
                else:
                    status, detail = (
                        "Needs refreshing",
                        "Your current plan needs review after changes to your details or service.",
                    )
            elif any(p.status == "Active" for p in plans):
                status, detail = "Previously used", "A newer routine has replaced this version."
            elif not usable:
                status, detail = (
                    "Needs refreshing",
                    "Your profile or service settings changed. This draft cannot be used.",
                )
            else:
                status, detail, needs = (
                    "Ready for review",
                    "Review the draft before changing your current routine.",
                    True,
                )
                pairs = list(
                    db.scalars(select(PlanPair).where(PlanPair.training_id.in_([p.id for p in plans])))
                )
                from app.activation import pair_fingerprint, plan_fingerprint

                digests = {p.id: plan_fingerprint(p) for p in plans}
                for pair in pairs:
                    training, nutrition = (
                        db.get(PlanVersion, pair.training_id),
                        db.get(PlanVersion, pair.nutrition_id),
                    )
                    if training and nutrition:
                        digests[pair.id] = pair_fingerprint(training, nutrition)
                subjects = [p.id for p in pairs] if pairs else [p.id for p in plans]
                approvals = list(db.scalars(select(Approval).where(Approval.subject_id.in_(subjects))))
                approvals = [a for a in approvals if a.fingerprint == digests.get(a.subject_id)]
                if any(a.decision == "Pending" and a.expires_at > now() for a in approvals):
                    status, detail, needs = (
                        "Studio review",
                        "The studio is reviewing this draft. Your current routine is unchanged.",
                        False,
                    )
                elif any(a.decision in {"Rejected", "Revise"} for a in approvals) and not any(
                    a.decision == "Approved" and a.expires_at > now() for a in approvals
                ):
                    status, detail, needs = (
                        "Needs revision",
                        "This draft needs changes before it can be used.",
                        False,
                    )
                elif (
                    group
                    and group.configuration.get("review_required")
                    and not any(a.decision == "Approved" and a.expires_at > now() for a in approvals)
                ):
                    status, detail, needs = (
                        "Studio review",
                        "A current studio approval is required before this draft can be used.",
                        False,
                    )
            if request.deadline <= now() and status not in {"In use", "Previously used"}:
                status, detail, needs = (
                    "Expired",
                    "This draft expired. Request a fresh update before using it.",
                    False,
                )
    if request.status in {"Queued", "Running"} and any(w.resume_count for w in works):
        status = (
            "Resuming your plans"
            if request.kind in {"Training", "Nutrition", "Combined"}
            else "Resuming your request"
        )
        detail = "Preparation is continuing from where it stopped. You don’t need to submit again."
    if plans and request.status in {"Waiting", "Stopped"} and request.kind == "Combined":
        detail = "Only part of your routine is ready. " + detail
    return {
        "id": request.id,
        "title": title,
        "status": status,
        "detail": detail,
        "needs_user_action": needs,
        "action": action,
        "conversation_id": request.session_id,
        "created_at": request.created_at.isoformat() + "Z",
        "can_cancel": request.status in {"Queued", "Running", "Waiting", "AwaitingConfirmation"},
        "in_progress": request.status in {"Queued", "Running"},
        "no_action_needed": request.status in {"Queued", "Running", "Waiting"},
        "auto_resume": request.status == "Waiting" and waiting_status(reason)[2],
        "stages": [
            {
                "role": role,
                "done": all(w.status == "Completed" for w in works if w.role == role),
                "running": any(w.status == "Running" for w in works if w.role == role),
            }
            for role in list(
                dict.fromkeys(w.role for w in (works[1:] if request.payload.get("routed") else works))
            )
        ]
        if request.kind != "Combined"
        else [
            {
                "role": role,
                "done": any(
                    p.kind == ("Training" if role == "Coach" else "Nutrition")
                    and p.status in {"Validated", "Active"}
                    for p in plans
                ),
                "running": any(w.role == role and w.status == "Running" for w in works),
            }
            for role in ["Coach", "Nutrition"]
        ],
    }


@router.get("/activity")
def activity(user: User = Depends(principal)):
    with transaction() as db:
        rows = db.scalars(
            select(Request).where(Request.user_id == user.id).order_by(Request.created_at.desc()).limit(100)
        )
        return [activity_view(db, row) for row in rows]


@router.get("/activity/{identifier}")
def activity_detail(identifier: str, user: User = Depends(principal)):
    with transaction() as db:
        row = db.scalar(select(Request).where(Request.id == identifier, Request.user_id == user.id))
        if not row:
            raise HTTPException(404, "Activity not found.")
        return activity_view(db, row)


@router.get("/plan-summary")
def summary(user: User = Depends(principal)):
    with transaction() as db:
        return plan_summary(db, user.id)


@router.get("/usage")
def usage(user: User = Depends(principal)):
    from app.call_limits import effective_account, entry, policy_lock, window_usage
    from app.model_catalog import catalog, model_info
    from app.models import AgentVersion
    from app.pricing import price_for

    settings = get_settings()
    connection = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        gate = ModelRateLimiter(connection)
        seconds, micros = connection.time()
        stamp = seconds * 1000 + micros // 1000
        day = connection.zrangebyscore(
            gate.account_key(user.id), stamp - 86400000 + 1, "+inf", withscores=True
        )
        with transaction() as db:
            policy_lock(db)
            allowance = effective_account(db, user.id)
            account_limit = allowance["limit"]
            member = db.scalar(select(Member).where(Member.user_id == user.id))
            group = db.get(MemberGroup, member.group_id) if member else None
            selected = {settings.default_model}
            for version_id in group.configuration.get("agent_versions", {}).values() if group else []:
                version = db.get(AgentVersion, version_id)
                if version:
                    selected.add(version.model)
            model_usage = []
            for info in catalog(settings):
                rule = entry(db, f"model:{info['provider']}:{info['id']}")
                counts = window_usage(
                    connection, gate.model_key(info["provider"], info["id"]), 3600, rule["limit"], stamp
                )
                model_usage.append(info | counts | {"limit": rule["limit"]})
            relevant = [row for row in model_usage if row["id"] in selected]
            status, reason = "Available", None
            blocking_scope = None
            if not settings.model_calls_enabled or not any(row["configured"] for row in relevant):
                status, reason = (
                    "Unavailable",
                    "AI assistance is currently unavailable. Your saved plans remain available.",
                )
            elif len(day) >= account_limit:
                status, reason = "Unavailable", "Your rolling 24-hour allowance is full."
                blocking_scope = "AccountDay"
            elif relevant and all(not row["configured"] or row["used"] >= row["limit"] for row in relevant):
                status, reason = (
                    "Unavailable",
                    "AI assistance is temporarily busy. Your saved requests will continue when service becomes available.",
                )
                blocking_scope = "ModelHour"
            elif connection.scard(gate.keys[2]) >= settings.model_concurrency_limit:
                status, reason = "Busy", "AI assistance is busy. Please try again shortly."
                blocking_scope = "Concurrency"
            scopes = ["global"] + ([f"group:{member.group_id}", f"member:{member.id}"] if member else [])
            accounts = list(db.scalars(select(BudgetAccount).where(BudgetAccount.scope_key.in_(scopes))))
            reservation = min(price_for(model).cost(8192, 2048) for model in selected)
            if len(accounts) < len(scopes) or any(
                a.limit_micros - a.reserved_micros - a.spent_micros < reservation for a in accounts
            ):
                status, reason = (
                    "Temporarily delayed",
                    "AI assistance is temporarily delayed. Your saved requests will continue when service becomes available.",
                )
                blocking_scope = "Spending"
        release_index = max(0, len(day) - account_limit)
        next_at = (
            datetime.fromtimestamp((day[release_index][1] + 86400000) / 1000, UTC).isoformat()
            if day
            else None
        )
        result = {
            "account_used": len(day),
            "account_limit": account_limit,
            "allowance_source": allowance["source"],
            "group_name": allowance.get("group_name"),
            "account_window_seconds": 86400,
            "shared_hourly_limit": next(
                row["limit"] for row in model_usage if row["id"] == settings.default_model
            ),
            "model_limits": [
                {
                    "model": row["id"],
                    "label": row["label"],
                    "limit": row["limit"],
                    "next_capacity_at": row["next_capacity_at"],
                }
                for row in relevant
            ],
            "model_next_capacity_at": min(
                (
                    row["next_capacity_at"]
                    for row in relevant
                    if row["configured"] and row["next_capacity_at"]
                ),
                default=None,
            ),
            "next_capacity_at": next_at,
            "service_availability": status,
            "blocking_reason": reason,
            "blocking_scope": blocking_scope,
            "observed_at": datetime.fromtimestamp(seconds, UTC).isoformat(),
        }
        result["blocking_until"] = (
            next_at
            if blocking_scope == "AccountDay"
            else result["model_next_capacity_at"]
            if blocking_scope == "ModelHour"
            else None
        )
        if user.role == "Owner":
            result["model_usage"] = model_usage
            result["default_provider"] = model_info(settings.default_model)["provider"]
            result["default_model"] = settings.default_model
        if user.role != "Owner":
            for key in ("shared_hourly_limit", "model_limits", "model_next_capacity_at", "blocking_scope"):
                result.pop(key, None)
            if blocking_scope != "AccountDay":
                result["blocking_until"] = None
        return result
    except RedisError:
        raise HTTPException(
            503, "Usage is temporarily unavailable. Your saved plans are still accessible."
        ) from None
    finally:
        connection.close()
