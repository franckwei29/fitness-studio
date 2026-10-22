"""The sole dispatch path combines policy, monetary budgets, and rate limits."""

from redis import Redis
from sqlalchemy import select

from app.admission import fingerprint
from app.agent_contracts import CHAT_TASK_INSTRUCTION
from app.agent_versions import verify_version
from app.budget import cancel_undispatched, mark_dispatching, mark_unknown, reserve, settle
from app.database import transaction
from app.gemini import GeminiTransport, ProviderFailure, build_payload
from app.models import AgentVersion, AuditEvent, ModelCall
from app.policy import PolicyDenied, execution_context
from app.pricing import UsageUncertain, price_for
from app.rate_limit import DispatchLimits, ModelRateLimiter
from app.settings import get_settings
from app.work_queue import lock_claim


class ModelUnavailable(Exception):
    """A fixed status explanation that does not require another model call."""


def generate(claim, instruction, context, output_type, max_output_tokens=2048):
    settings = get_settings()
    if not settings.model_calls_enabled:
        raise ModelUnavailable("Model calls are disabled.")
    if not settings.gemini_api_key.get_secret_value():
        raise ModelUnavailable("Gemini credentials are not configured.")
    model = settings.gemini_model
    with transaction() as db:
        item, request = lock_claim(db, claim)
        authorized = execution_context(db, item, request)
        version_id = context.get("agent_version_id")
        if version_id:
            if item.input_versions.get("agent_version") != version_id:
                raise PolicyDenied("The model context does not match the task agent version.")
            version = verify_version(db.get(AgentVersion, version_id), item.role)
            model, instruction = version.model, version.prompt
        if request.kind == "Chat" and item.role in {"Coach", "Nutrition"}:
            instruction += CHAT_TASK_INSTRUCTION
        price = price_for(model)
        payload = build_payload(model, instruction, context, output_type, max_output_tokens)
        digest = fingerprint({"work_id": claim.work_id, "payload": payload})
        previous = db.scalar(
            select(ModelCall)
            .where(
                ModelCall.request_id == request.id,
                ModelCall.request_fingerprint == digest,
                ModelCall.status == "Settled",
                ModelCall.output.is_not(None),
            )
            .order_by(ModelCall.created_at.desc())
            .limit(1)
        )
        if previous and previous.output is not None:
            return output_type.model_validate(previous.output), previous.id
    call_id = reserve(
        claim.request_id,
        claim.role,
        model,
        price.reservation(),
        price.version,
        authorized["scope_keys"],
        request_fingerprint=digest,
    )
    connection = Redis.from_url(settings.redis_url)
    gate = ModelRateLimiter(connection)
    granted = False
    dispatched = False
    try:
        permit = gate.acquire(
            call_id,
            DispatchLimits(
                settings.model_rpm_limit, settings.model_daily_call_limit, settings.model_concurrency_limit
            ),
        )
        if permit != "Granted":
            raise ModelUnavailable(f"Model dispatch is waiting: {permit}.")
        granted = True
        with transaction() as db:
            item, request = lock_claim(db, claim)
            execution_context(db, item, request)
        if not mark_dispatching(call_id):
            raise ModelUnavailable("This model call has already been dispatched.")
        dispatched = True
        try:
            result = GeminiTransport(settings.gemini_api_key).send(payload, output_type)
            actual = price.actual(result.usage)
        except (ProviderFailure, UsageUncertain) as exc:
            with transaction() as db:
                db.add(
                    AuditEvent(
                        actor_id="system",
                        request_id=claim.request_id,
                        event_type="model.response_failed",
                        subject_id=call_id,
                        details={
                            "reason": str(exc),
                            "http_status": getattr(exc, "http_status", None),
                            "provider_id": getattr(exc, "provider_id", None),
                            "usage": getattr(exc, "usage", None),
                        },
                    )
                )
            mark_unknown(call_id)
            # Keep the concurrency slot until this uncertain call is reconciled.
            raise
        settle(call_id, actual, result.usage, result.provider_id, result.output.model_dump(mode="json"))
        gate.release(call_id)
        return result.output, call_id
    except Exception:
        if not dispatched:
            cancel_undispatched(call_id)
            if granted:
                gate.release(call_id)
        raise
    finally:
        connection.close()
