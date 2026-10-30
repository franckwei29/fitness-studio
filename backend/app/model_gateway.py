"""The sole dispatch path combines policy, monetary budgets, and rate limits."""

from redis import Redis
from sqlalchemy import select

from app.admission import fingerprint
from app.agent_contracts import CHAT_TASK_INSTRUCTION
from app.agent_versions import verify_version
from app.budget import cancel_undispatched, mark_dispatching, mark_unknown, reserve, settle
from app.call_limits import dispatch_limits
from app.database import transaction
from app.gemini import GeminiTransport, ProviderFailure
from app.model_catalog import model_info
from app.model_payload import build_payload
from app.models import AgentVersion, AuditEvent, ModelCall
from app.openai_provider import OpenAITransport
from app.policy import PolicyDenied, execution_context
from app.pricing import UsageUncertain, price_for
from app.rate_limit import ModelRateLimiter
from app.settings import get_settings
from app.work_queue import lock_claim


class ModelUnavailable(Exception):
    """A fixed status explanation that does not require another model call."""

    def __init__(self, message, wait_reason="ModelDispatchUnavailable", context=None):
        super().__init__(message)
        self.wait_reason = wait_reason
        self.context = context or {}


def generate(claim, instruction, context, output_type, max_output_tokens=2048):
    settings = get_settings()
    if not settings.model_calls_enabled:
        raise ModelUnavailable("Model calls are disabled.")
    model = settings.default_model
    version = None
    with transaction() as db:
        item, request = lock_claim(db, claim)
        authorized = execution_context(db, item, request)
        version_id = context.get("agent_version_id")
        if version_id:
            if item.input_versions.get("agent_version") != version_id:
                raise PolicyDenied("The model context does not match the task agent version.")
            version = verify_version(db.get(AgentVersion, version_id), item.role)
            model, instruction = version.model, version.prompt
        if request.kind == "Auto":
            from app.intent_router import AUTO_INSTRUCTION

            instruction += "\n" + AUTO_INSTRUCTION
        if request.kind == "Chat" and item.role in {"Coach", "Nutrition"}:
            instruction += CHAT_TASK_INSTRUCTION
        if item.role == "Nutrition" and output_type.__name__ == "MealCompatibilityReview":
            from app.nutrition_runtime import REVIEW_INSTRUCTION

            instruction += "\n" + REVIEW_INSTRUCTION
        if request.payload.get("progressive"):
            instruction += "\nThis is a staged draft discussion. Follow member_draft_feedback using draft_to_revise as the starting point. Do not claim another specialist has started or that anything is activated. Respect the original goal and confirmed constraints."
        price = price_for(model)
        provider = model_info(model)["provider"]
        payload = build_payload(model, instruction, context, output_type, max_output_tokens, version=version)
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
    if not settings.credentials(provider).get_secret_value():
        raise ModelUnavailable(f"{provider.title()} credentials are not configured.")
    reservation_micros = price.reservation_for(payload)
    call_id = reserve(
        claim.request_id,
        claim.role,
        model,
        reservation_micros,
        price.version,
        authorized["scope_keys"],
        request_fingerprint=digest,
        provider=provider,
    )
    connection = Redis.from_url(settings.redis_url)
    gate = ModelRateLimiter(connection)
    granted = False
    dispatched = False
    try:
        with transaction() as db:
            limits = dispatch_limits(db, request.user_id, provider, model)
            permit = gate.acquire(call_id, limits, request.user_id, provider=provider, model=model)
            if permit != "Granted":
                raise ModelUnavailable(
                    f"Model dispatch is waiting: {permit}.",
                    {"Hour": "HourlyCallLimit", "AccountDay": "AccountDailyCallLimit"}.get(
                        permit, "ModelDispatchUnavailable"
                    ),
                    {"model": model, "provider": provider, "amount_micros": reservation_micros},
                )
            granted = True
            if not mark_dispatching(call_id, claim=claim):
                raise ModelUnavailable("This model call has already been dispatched.")
            dispatched = True
        try:
            transport = OpenAITransport if provider == "openai" else GeminiTransport
            result = transport(settings.credentials(provider)).send(payload, output_type)
            actual = price.actual(result.usage)
            if actual > reservation_micros:
                raise UsageUncertain("Reported usage exceeds the request reservation bound.")
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
                gate.cancel_undispatched(call_id, request.user_id, provider=provider, model=model)
        raise
    finally:
        connection.close()
