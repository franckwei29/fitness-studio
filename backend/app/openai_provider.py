"""Single-attempt, nonstored OpenAI Responses requests with strict local validation."""

import json

import httpx

from app.gemini import ProviderFailure, ProviderResult

ENDPOINT = "https://api.openai.com/v1/responses"


def strict_schema(value):
    if isinstance(value, list):
        return [strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: strict_schema(item) for key, item in value.items() if key != "default"}
    if result.get("type") == "object":
        result["additionalProperties"] = False
        result["required"] = list(result.get("properties", {}))
    return result


def build_payload(model, instruction, context, output_type, max_output_tokens=2048, reasoning_effort=None):
    from app.model_catalog import validate_options

    validate_options(model, "openai", max_output_tokens, reasoning_effort)
    if not instruction:
        raise ValueError("Model instructions are required.")
    payload = {
        "model": model,
        "instructions": instruction,
        "input": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        "max_output_tokens": max_output_tokens,
        "reasoning": {"effort": reasoning_effort or "low"},
        "text": {
            "format": {
                "type": "json_schema",
                "name": output_type.__name__,
                "strict": True,
                "schema": strict_schema(output_type.model_json_schema()),
            }
        },
        "store": False,
        "stream": False,
        "service_tier": "default",
        "prompt_cache_options": {"ttl": "30m"},
    }
    if len(json.dumps(payload, ensure_ascii=False).encode()) > 64000:
        raise ValueError("The model request exceeds the input size limit.")
    return payload


def decode_response(body, output_type):
    if not isinstance(body, dict) or body.get("status") != "completed":
        raise ProviderFailure("The model did not complete the request.")
    texts = []
    if not isinstance(body.get("output"), list):
        raise ProviderFailure("The model returned an unsupported response format.")
    for item in body["output"]:
        if not isinstance(item, dict):
            raise ProviderFailure("The model returned invalid content.")
        if item.get("type") == "reasoning":
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            raise ProviderFailure("The model returned an unexpected action.")
        if not isinstance(item.get("content"), list):
            raise ProviderFailure("The model returned invalid content.")
        for content in item["content"]:
            if (
                not isinstance(content, dict)
                or content.get("type") != "output_text"
                or not isinstance(content.get("text"), str)
            ):
                raise ProviderFailure("The model could not provide the requested answer.")
            texts.append(content["text"])
    try:
        output = output_type.model_validate_json("".join(texts))
    except ValueError:
        raise ProviderFailure("The model output did not satisfy its schema.") from None
    if not isinstance(body.get("id"), str) or not body["id"] or not isinstance(body.get("usage"), dict):
        raise ProviderFailure("The model response is missing accounting metadata.")
    return ProviderResult(body["id"], output, body["usage"])


class OpenAITransport:
    def __init__(self, api_key, transport=None):
        self.api_key, self.transport = api_key, transport

    def send(self, payload, output_type):
        if not self.api_key.get_secret_value():
            raise ValueError("OpenAI credentials are not configured.")
        try:
            with httpx.Client(
                timeout=httpx.Timeout(90, connect=10), follow_redirects=False, transport=self.transport
            ) as client:
                response = client.post(
                    ENDPOINT,
                    json=payload,
                    headers={"Authorization": "Bearer " + self.api_key.get_secret_value()},
                )
            if response.status_code != 200:
                raise ProviderFailure(
                    f"OpenAI returned HTTP {response.status_code}.", http_status=response.status_code
                )
            body = response.json()
        except (httpx.HTTPError, ValueError):
            raise ProviderFailure("OpenAI did not return a readable response.") from None
        try:
            return decode_response(body, output_type)
        except ProviderFailure as exc:
            if isinstance(body, dict):
                exc.usage, exc.provider_id = body.get("usage"), body.get("id")
            raise
