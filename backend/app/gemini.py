"""Single-attempt Gemini Interactions transport and strict output decoding."""

import json
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, SecretStr

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"


class ProviderFailure(Exception):
    """Sanitized provider error; outbound failures may still have incurred usage."""

    def __init__(self, message, *, http_status=None, usage=None, provider_id=None):
        super().__init__(message)
        self.http_status = http_status
        self.usage = usage
        self.provider_id = provider_id


@dataclass(frozen=True)
class ProviderResult:
    provider_id: str | None
    output: BaseModel
    usage: dict


def provider_schema(schema):
    """Normalize nullable unions while preserving strict local validation."""
    if isinstance(schema, list):
        return [provider_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    result = {
        key: provider_schema(value)
        for key, value in schema.items()
        if key not in {"minItems", "maxItems", "default"}
    }
    if result.get("type") == "object" and isinstance(result.get("properties"), dict):
        result["required"] = list(result["properties"])
    branches = result.get("anyOf", [])
    if len(branches) == 2:
        nonnull = [branch for branch in branches if branch.get("type") != "null"]
        if len(nonnull) == 1 and isinstance(nonnull[0].get("type"), str):
            result.pop("anyOf")
            result.update(nonnull[0])
            result["type"] = [nonnull[0]["type"], "null"]
    return result


def build_payload(model, instruction, context, output_type, max_output_tokens=2048):
    if not model or not instruction or not 64 <= max_output_tokens <= 4096:
        raise ValueError("Invalid model request configuration.")
    payload = {
        "model": model,
        "system_instruction": instruction,
        "input": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": provider_schema(output_type.model_json_schema()),
        },
        "generation_config": {"max_output_tokens": max_output_tokens},
        "store": False,
        "stream": False,
    }
    if len(json.dumps(payload, ensure_ascii=False).encode()) > 64000:
        raise ValueError("The model request exceeds the input size limit.")
    return payload


def decode_response(body, output_type):
    if not isinstance(body, dict) or body.get("status") != "completed":
        raise ProviderFailure("The model did not complete the request.")
    steps = body.get("steps")
    if not isinstance(steps, list):
        raise ProviderFailure("The model returned an unsupported response format.")
    texts = []
    for step in steps:
        if not isinstance(step, dict):
            raise ProviderFailure("The model returned an invalid response step.")
        if step.get("type") == "model_output":
            content = step.get("content")
            if not isinstance(content, list):
                raise ProviderFailure("The model returned invalid content.")
            for item in content:
                if (
                    not isinstance(item, dict)
                    or item.get("type") != "text"
                    or not isinstance(item.get("text"), str)
                ):
                    raise ProviderFailure("The model returned unexpected content.")
                texts.append(item["text"])
        elif step.get("type") != "thought":
            raise ProviderFailure("The model returned an unexpected action.")
    try:
        output = output_type.model_validate_json("".join(texts))
    except ValueError:
        raise ProviderFailure("The model output did not satisfy its schema.") from None
    provider_id = body.get("id")
    usage = body.get("usage")
    if (provider_id is not None and (not isinstance(provider_id, str) or not provider_id)) or not isinstance(
        usage, dict
    ):
        raise ProviderFailure("The model response is missing accounting metadata.")
    return ProviderResult(provider_id, output, usage)


class GeminiTransport:
    def __init__(self, api_key: SecretStr, transport=None):
        self.api_key = api_key
        self.transport = transport

    def send(self, payload, output_type):
        if not self.api_key.get_secret_value():
            raise ValueError("Gemini credentials are not configured.")
        # The caller must hold a dispatch permit and a committed budget reservation.
        # HTTPX retries default to zero; never retry within this transport.
        try:
            with httpx.Client(
                timeout=httpx.Timeout(90, connect=10), follow_redirects=False, transport=self.transport
            ) as client:
                response = client.post(
                    ENDPOINT,
                    json=payload,
                    headers={
                        "x-goog-api-key": self.api_key.get_secret_value(),
                    },
                )
            if response.status_code != 200:
                raise ProviderFailure(
                    f"Gemini returned HTTP {response.status_code}.", http_status=response.status_code
                )
            body = response.json()
        except (httpx.HTTPError, ValueError):
            raise ProviderFailure("Gemini did not return a readable response.") from None
        try:
            return decode_response(body, output_type)
        except ProviderFailure as exc:
            if isinstance(body, dict):
                exc.usage = body.get("usage")
                exc.provider_id = body.get("id")
            raise
