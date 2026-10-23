"""Member-facing, read-only projections. Internal workflow states stay internal."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select

from app.database import transaction
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
                "Nutrition": "Nutrition Assistant is preparing your meals.",
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
        status = "Assistance paused"
        detail = {
            "BudgetRequired": "AI assistance is paused. The studio needs to review its allowance.",
            "HourlyCallLimit": "The shared hourly allowance is full. The studio can resume this update when capacity returns.",
            "AccountDailyCallLimit": "Your rolling 24-hour allowance is full. Existing plans are still available.",
            "UsageReconciliation": "A response could not be confirmed. The studio needs to review it before retrying.",
            "PolicyReview": "Your details or service settings need a studio review.",
        }.get(reason, "The studio needs to review this update before it can continue.")
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
    settings = get_settings()
    connection = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        gate = ModelRateLimiter(connection)
        seconds, micros = connection.time()
        stamp = seconds * 1000 + micros // 1000
        # Read the legacy ledger too: historical dispatches must remain visible.
        day = connection.zrangebyscore(
            gate.account_key(user.id), stamp - 86400000 + 1, "+inf", withscores=True
        )
        hour = connection.zcount(gate.keys[0], stamp - 3600000 + 1, "+inf")
        legacy_hour = connection.zcount(gate.keys[1], stamp - 3600000 + 1, "+inf")
        active = connection.scard(gate.keys[2])
        status, reason = "Available", None
        if not settings.model_calls_enabled or not settings.gemini_api_key.get_secret_value():
            status, reason = (
                "Unavailable",
                "AI assistance is currently disabled. Your saved plans remain available.",
            )
        elif len(day) >= settings.model_account_daily_call_limit:
            status, reason = "Unavailable", "Your rolling 24-hour allowance is full."
        elif max(hour, legacy_hour) >= settings.model_hourly_call_limit:
            status, reason = "Unavailable", "The studio's shared hourly allowance is full."
        elif active >= settings.model_concurrency_limit:
            status, reason = "Busy", "AI assistance is busy. An interrupted response may need studio review."
        with transaction() as db:
            member = db.scalar(select(Member).where(Member.user_id == user.id))
            scopes = ["global"] + ([f"group:{member.group_id}", f"member:{member.id}"] if member else [])
            accounts = list(db.scalars(select(BudgetAccount).where(BudgetAccount.scope_key.in_(scopes))))
            from app.pricing import price_for

            reservation = price_for(settings.gemini_model).reservation()
            if len(accounts) < len(scopes) or any(
                a.limit_micros - a.reserved_micros - a.spent_micros < reservation for a in accounts
            ):
                status, reason = "Paused", "The studio needs to review the available AI budget."
        next_at = datetime.fromtimestamp((day[0][1] + 86400000) / 1000, UTC).isoformat() if day else None
        return {
            "account_used": len(day),
            "account_limit": settings.model_account_daily_call_limit,
            "account_window_seconds": 86400,
            "shared_hourly_limit": settings.model_hourly_call_limit,
            "next_capacity_at": next_at,
            "service_availability": status,
            "blocking_reason": reason,
            "observed_at": datetime.fromtimestamp(seconds, UTC).isoformat(),
        }
    except RedisError:
        raise HTTPException(
            503, "Usage is temporarily unavailable. Your saved plans are still accessible."
        ) from None
    finally:
        connection.close()
