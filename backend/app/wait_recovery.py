"""Resume original work only when fresh policy and dispatch checks permit it."""

from datetime import timedelta

from redis import Redis
from sqlalchemy import or_, select

from app.call_limits import dispatch_limits
from app.database import transaction
from app.model_catalog import model_info
from app.models import AgentVersion, BudgetAccount, ModelCall, Request, WorkItem, now
from app.policy import PolicyDenied, execution_context
from app.pricing import price_for
from app.rate_limit import ModelRateLimiter
from app.settings import get_settings
from app.work_queue import event

RECOVERABLE = {
    "BudgetRequired",
    "HourlyCallLimit",
    "AccountDailyCallLimit",
    "ModelDispatchUnavailable",
    "PolicyReview",
}


def ready(db, item, request, connection):
    settings = get_settings()
    if request.model_calls >= request.max_model_calls or not settings.model_calls_enabled:
        return False
    # Uncertain billing and in-flight calls must never be retried by a capacity change.
    if db.scalar(
        select(ModelCall.id)
        .where(
            ModelCall.request_id == request.id,
            ModelCall.status.in_(["Reserved", "Dispatching", "UsageUnknown"]),
        )
        .limit(1)
    ):
        return False
    try:
        context = execution_context(db, item, request)
    except PolicyDenied:
        if item.wait_reason != "PolicyReview":
            item.wait_reason = "PolicyReview"
            event(db, request, "waiting", status="Waiting", reason="PolicyReview", role=item.role)
        return False
    version = (
        db.get(AgentVersion, item.input_versions["agent_version"])
        if item.input_versions.get("agent_version")
        else None
    )
    model = version.model if version else settings.default_model
    provider = model_info(model)["provider"]
    if not settings.credentials(provider).get_secret_value():
        return False
    previous = item.wait_context or {}
    # Legacy waits have no exact reservation. Use the model's conservative bound;
    # the dispatch path still reserves the actual payload amount atomically.
    amount = previous.get("amount_micros") if previous.get("model") == model else None
    amount = amount if type(amount) is int and amount > 0 else price_for(model).reservation()
    scopes = set(context["scope_keys"]) | {"global"}
    accounts = list(db.scalars(select(BudgetAccount).where(BudgetAccount.scope_key.in_(scopes))))
    if len(accounts) != len(scopes) or any(
        a.currency != "USD" or a.limit_micros - a.spent_micros - a.reserved_micros < amount for a in accounts
    ):
        return False
    limits = dispatch_limits(db, request.user_id, provider, model)
    gate = ModelRateLimiter(connection)
    seconds, microseconds = connection.time()
    stamp = seconds * 1000 + microseconds // 1000
    return (
        connection.scard(gate.keys[2]) < limits.concurrent
        and connection.zcount(gate.model_key(provider, model), f"({stamp - 3600000}", "+inf")
        < limits.per_hour
        and connection.zcount(gate.account_key(request.user_id), f"({stamp - 86400000}", "+inf")
        < limits.per_account_day
    )


def resume_waiting(*, force=False, batch_size=50):
    """No provider calls, budget releases, deadline extension or completed-step resets."""
    if not 1 <= batch_size <= 100:
        raise ValueError("Recovery batch size must be between 1 and 100.")
    connection = Redis.from_url(get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2)
    resumed = 0
    try:
        with transaction() as db:
            query = select(WorkItem.id).where(
                WorkItem.status == "Waiting", WorkItem.wait_reason.in_(RECOVERABLE)
            )
            if not force:
                query = query.where(or_(WorkItem.resume_after.is_(None), WorkItem.resume_after <= now()))
            identifiers = list(
                db.scalars(query.order_by(WorkItem.resume_after, WorkItem.id).limit(batch_size))
            )
        for identifier in identifiers:
            with transaction() as db:
                item = db.scalar(
                    select(WorkItem).where(WorkItem.id == identifier).with_for_update(skip_locked=True)
                )
                if not item or item.status != "Waiting" or item.wait_reason not in RECOVERABLE:
                    continue
                if not force and item.resume_after and item.resume_after > now():
                    continue
                request = db.scalar(select(Request).where(Request.id == item.request_id).with_for_update())
                if request.status != "Waiting" or request.deadline <= now():
                    continue
                item.resume_count += 1
                item.resume_after = now() + timedelta(seconds=min(300, 30 * 2 ** min(item.resume_count, 4)))
                if not ready(db, item, request, connection):
                    continue
                item.status = request.status = "Queued"
                item.wait_reason = None
                item.lease_until = None
                item.fence += 1
                item.resume_after = None
                event(db, request, "recovery_queued", status="Queued", role=item.role)
                resumed += 1
    finally:
        connection.close()
    return resumed
