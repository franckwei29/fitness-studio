"""Persistent call policies shared by web, chat and dispatch admission."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.mysql import insert

from app.admission import fingerprint
from app.database import transaction
from app.model_catalog import MODELS, catalog
from app.models import AuditEvent, CallLimit, Member, MemberGroup, User, now, uid
from app.rate_limit import DispatchLimits, ModelRateLimiter
from app.security import owner
from app.settings import get_settings

router = APIRouter(prefix="/api/owner/call-limits")


def seed(db, scope, calls):
    db.execute(
        insert(CallLimit)
        .values(id=uid(), created_at=now(), scope_key=scope, calls=calls, version=1)
        .prefix_with("IGNORE")
    )
    return db.scalar(
        select(CallLimit).where(CallLimit.scope_key == scope).execution_options(populate_existing=True)
    )


def policy_lock(db):
    # One short lock orders policy edits, group moves and Redis admission. Never
    # hold it during a provider request, budget reservation or task execution.
    row = db.scalar(select(CallLimit).where(CallLimit.scope_key == "initial").with_for_update())
    if not row:
        seed(db, "initial", get_settings().model_account_daily_call_limit)
        row = db.scalar(select(CallLimit).where(CallLimit.scope_key == "initial").with_for_update())
    return row


def initialize(db):
    settings = get_settings()
    initial = policy_lock(db)
    for model, info in MODELS.items():
        seed(db, f"model:{info['provider']}:{model}", settings.model_hourly_call_limit)
    for identifier in db.scalars(select(MemberGroup.id)):
        seed(db, f"group:{identifier}", initial.calls)
    for identifier in db.scalars(select(User.id).where(User.role == "Owner")):
        seed(db, f"owner:{identifier}", initial.calls)


def row_for(db, scope):
    row = db.scalar(select(CallLimit).where(CallLimit.scope_key == scope))
    if row:
        return row
    initial = db.scalar(select(CallLimit).where(CallLimit.scope_key == "initial"))
    if not initial:
        initialize(db)
        initial = db.scalar(select(CallLimit).where(CallLimit.scope_key == "initial"))
    kind, _, identifier = scope.partition(":")
    if kind == "member" and db.get(Member, identifier):
        return seed(db, scope, None)
    if kind == "group" and db.get(MemberGroup, identifier):
        return seed(db, scope, initial.calls)
    if kind == "owner":
        account = db.get(User, identifier)
        if account and account.role == "Owner":
            return seed(db, scope, initial.calls)
    if kind == "model" and scope in {f"model:{v['provider']}:{m}" for m, v in MODELS.items()}:
        # New catalog entries are initialized at deployment, before serving work.
        return seed(db, scope, get_settings().model_hourly_call_limit)
    raise HTTPException(404, "Call allowance not found.")


def effective_account(db, user_id):
    account = db.get(User, user_id)
    if not account:
        raise HTTPException(404, "Account not found.")
    if account.role == "Owner":
        row = row_for(db, f"owner:{account.id}")
        return {"limit": row.calls, "source": "Owner allowance", "scope_key": row.scope_key}
    member = db.scalar(select(Member).where(Member.user_id == user_id))
    if member:
        row = row_for(db, f"member:{member.id}")
        group = db.get(MemberGroup, member.group_id)
        default = row_for(db, f"group:{member.group_id}")
        return {
            "limit": row.calls if row.calls is not None else default.calls,
            "source": "Custom allowance" if row.calls is not None else "Group default",
            "scope_key": row.scope_key,
            "group_id": member.group_id,
            "group_name": group.name,
            "group_limit": default.calls,
        }
    row = row_for(db, "initial")
    return {"limit": row.calls, "source": "Initial account allowance", "scope_key": "initial"}


def dispatch_limits(db, user_id, provider, model):
    policy_lock(db)
    return DispatchLimits(
        row_for(db, f"model:{provider}:{model}").calls,
        effective_account(db, user_id)["limit"],
        get_settings().model_concurrency_limit,
    )


def entry(db, scope):
    row = row_for(db, scope)
    data = {"scope_key": scope, "calls": row.calls, "version": row.version}
    result = dict(data)
    if scope.startswith("model:"):
        _, provider, model = scope.split(":", 2)
        result |= {
            "label": MODELS[model]["label"],
            "provider": provider,
            "model": model,
            "source": "Studio model limit",
            "applies_to": "All studio accounts",
            "limit": row.calls,
        }
    elif scope.startswith("group:"):
        group_id = scope[6:]
        count = db.scalar(select(func.count()).select_from(Member).where(Member.group_id == group_id))
        custom = db.scalar(
            select(func.count())
            .select_from(Member)
            .join(CallLimit, CallLimit.scope_key == func.concat("member:", Member.id))
            .where(Member.group_id == group_id, CallLimit.calls.is_not(None))
        )
        result |= {
            "label": db.get(MemberGroup, group_id).name,
            "members": count,
            "inheriting_members": count - custom,
            "source": "Group default",
            "applies_to": "Each member separately",
            "limit": row.calls,
        }
    elif scope.startswith("member:"):
        member = db.get(Member, scope[7:])
        account = db.get(User, member.user_id)
        allowance = effective_account(db, account.id)
        group_row = row_for(db, f"group:{member.group_id}")
        data |= {"group_id": member.group_id, "group_version": group_row.version}
        result |= allowance | {
            "label": account.display_name,
            "email": account.email,
            "member_id": member.id,
            "user_id": account.id,
            "applies_to": "This member only",
        }
    elif scope.startswith("owner:"):
        result |= {
            "label": db.get(User, scope[6:]).display_name,
            "source": "Owner allowance",
            "applies_to": "This owner account only",
            "limit": row.calls,
        }
    else:
        result |= {
            "label": "Initial account allowance",
            "source": "Initial account allowance",
            "applies_to": "Each account before membership",
            "limit": row.calls,
        }
    return result | {"fingerprint": fingerprint(data)}


class LimitChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_key: str = Field(min_length=1, max_length=200)
    calls: int | None = Field(ge=1, le=1000000, strict=True)
    expected_fingerprint: str = Field(min_length=64, max_length=64)
    reason: str = Field(min_length=1, max_length=1000)


def change(db, body, actor):
    policy_lock(db)
    before = entry(db, body.scope_key)
    if before["fingerprint"] != body.expected_fingerprint:
        raise HTTPException(409, "This allowance or member group changed. Refresh before saving.")
    if body.calls is None and not body.scope_key.startswith("member:"):
        raise HTTPException(422, "Only a member can restore the group default.")
    row = row_for(db, body.scope_key)
    row.calls = body.calls
    db.info["resume_waiting"] = True
    row.version += 1
    db.flush()
    after = entry(db, body.scope_key)
    db.add(
        AuditEvent(
            actor_id=actor.id,
            event_type="call_limit.changed",
            subject_id=row.id,
            details={
                "scope_key": row.scope_key,
                "label": before["label"],
                "previous_calls": before["limit"],
                "calls": after["limit"],
                "source": after["source"],
                "previous_source": before["source"],
                "reason": body.reason,
                "version": row.version,
            },
        )
    )
    noun = "Hourly limit" if row.scope_key.startswith("model:") else "Daily allowance"
    message = f"{noun} updated from {before['limit']} to {after['limit']} calls."
    if body.calls is None:
        message = f"Group default restored: {after['limit']} calls per day."
    if row.scope_key.startswith("group:"):
        message += f" Applies to {before['inheriting_members']} members. Personal exceptions are unchanged."
    return after | {"message": message, "usage_unchanged": True}


def window_usage(connection, key, seconds, limit, stamp=None):
    if stamp is None:
        sec, micro = connection.time()
        stamp = sec * 1000 + micro // 1000
    scores = connection.zrangebyscore(key, stamp - seconds * 1000 + 1, "+inf", withscores=True)
    used = len(scores)
    next_at = None
    if used >= limit:
        next_at = datetime.fromtimestamp((scores[used - limit][1] + seconds * 1000) / 1000, UTC).isoformat()
    return {"used": used, "next_capacity_at": next_at}


@router.get("/entry")
def get_entry(scope: str, user: User = Depends(owner)):
    with transaction() as db:
        policy_lock(db)
        return entry(db, scope)


@router.post("")
def save_limit(body: LimitChange, user: User = Depends(owner)):
    with transaction() as db:
        return change(db, body, user)


@router.get("")
def overview(
    query: str = Query(default="", max_length=200),
    group_id: str | None = None,
    custom_only: bool = False,
    offset: int = Query(default=0, ge=0),
    user: User = Depends(owner),
):
    settings = get_settings()
    with transaction() as db:
        policy_lock(db)
        groups = [
            entry(db, f"group:{g.id}") for g in db.scalars(select(MemberGroup).order_by(MemberGroup.name))
        ]
        statement = (
            select(Member)
            .join(User, Member.user_id == User.id)
            .join(MemberGroup, Member.group_id == MemberGroup.id)
        )
        if query.strip():
            term = f"%{query.strip().replace('%', '').replace('_', '')}%"
            statement = statement.where(
                or_(User.display_name.ilike(term), User.email.ilike(term), MemberGroup.name.ilike(term))
            )
        if group_id:
            statement = statement.where(Member.group_id == group_id)
        if custom_only:
            statement = statement.join(
                CallLimit, CallLimit.scope_key == func.concat("member:", Member.id)
            ).where(CallLimit.calls.is_not(None))
        total = db.scalar(select(func.count()).select_from(statement.subquery()))
        members = [
            entry(db, f"member:{m.id}")
            for m in db.scalars(statement.order_by(User.display_name, Member.id).offset(offset).limit(50))
        ]
        models = [entry(db, f"model:{info['provider']}:{info['id']}") | info for info in catalog(settings)]
        result = {
            "models": models,
            "groups": groups,
            "members": members,
            "total": total,
            "offset": offset,
            "has_more": offset + len(members) < total,
            "owner": entry(db, f"owner:{user.id}"),
            "initial": entry(db, "initial"),
            "default_model": settings.default_model,
        }
    connection = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        gate = ModelRateLimiter(connection)
        for model in models:
            model |= window_usage(
                connection, gate.model_key(model["provider"], model["model"]), 3600, model["limit"]
            )
        for member in members:
            member |= window_usage(connection, gate.account_key(member["user_id"]), 86400, member["limit"])
        result["owner"] |= window_usage(
            connection, gate.account_key(user.id), 86400, result["owner"]["limit"]
        )
    except RedisError:
        result["usage_error"] = "Usage is temporarily unavailable. Limits can still be reviewed."
    finally:
        connection.close()
    return result
