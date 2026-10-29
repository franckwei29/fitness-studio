"""Build the same provider request for dispatch and later evidence verification."""

from app.gemini import build_payload as gemini_payload
from app.model_catalog import model_info, validate_options
from app.openai_provider import build_payload as openai_payload


def build_payload(model, instruction, context, output_type, max_output_tokens=2048, *, version=None):
    provider = model_info(model)["provider"]
    effort = getattr(version, "reasoning_effort", None)
    # Legacy versions keep their original task-specific output limit and fingerprint.
    configured_limit = getattr(version, "max_output_tokens", None)
    if configured_limit is not None:
        max_output_tokens = min(max_output_tokens, configured_limit)
    validate_options(model, getattr(version, "provider", None), max_output_tokens, effort)
    if provider == "openai":
        return openai_payload(model, instruction, context, output_type, max_output_tokens, effort)
    return gemini_payload(model, instruction, context, output_type, max_output_tokens)
