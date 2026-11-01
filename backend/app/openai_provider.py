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
    return payload


def decode_response(body, output_type):
    if not isinstance(body, dict) or body.get("status") != "completed":
        raise ProviderFailure("The model did not complete the request.")
    texts = []
    search_calls = 0
    partial_page = False
    if not isinstance(body.get("output"), list):
        raise ProviderFailure("The model returned an unsupported response format.")
    for item in body["output"]:
        if not isinstance(item, dict):
            raise ProviderFailure("The model returned invalid content.")
        if item.get("type") == "web_search_call":
            action = (item.get("action") or {}).get("type")
            if item.get("status") != "completed":
                if action in {"open_page", "find_in_page"}:
                    partial_page = True
                    continue
                raise ProviderFailure("The web search did not complete.")
            if action == "search":
                search_calls += 1
            continue
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
    if partial_page and (not search_calls or not any(
        a.get("type") == "url_citation"
        for item in body["output"] if item.get("type") == "message"
        for content in item.get("content", []) for a in content.get("annotations", [])
    )):
        raise ProviderFailure("The search did not provide sufficient cited evidence.")
    if not "".join(texts).strip():
        raise ProviderFailure("The search returned no usable text.")
    try:
        output = (
            output_type.from_search_text("".join(texts))
            if hasattr(output_type, "from_search_text")
            else output_type.model_validate_json("".join(texts))
        )
    except ValueError as exc:
        # Field names and validation codes aid diagnosis without logging model text.
        errors = (
            exc.errors(include_input=False, include_context=False, include_url=False)
            if hasattr(exc, "errors")
            else []
        )
        fields = ", ".join(".".join(map(str, e["loc"])) + ":" + e["type"] for e in errors[:5])
        raise ProviderFailure(
            "The model output did not satisfy its schema." + (" " + fields if fields else "")
        ) from None
    if not isinstance(body.get("id"), str) or not body["id"] or not isinstance(body.get("usage"), dict):
        raise ProviderFailure("The model response is missing accounting metadata.")
    usage = dict(body["usage"])
    reported = (body.get("tool_usage") or {}).get("web_search", {}).get("num_requests")
    if reported is not None:
        if type(reported) is not int or reported < 0:
            raise ProviderFailure("Invalid search accounting metadata.")
        search_calls = reported
    if search_calls:
        usage["web_search_calls"] = search_calls
    return ProviderResult(body["id"], output, usage)


class OpenAITransport:
    def __init__(self, api_key, transport=None):
        self.api_key, self.transport = api_key, transport

    def count_input_tokens(self, payload):
        # Count only supported request fields; preserve the full model input and schema.
        fields = {"model", "instructions", "input", "text", "tools", "tool_choice", "reasoning"}
        try:
            with httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=False,
                              transport=self.transport) as client:
                response = client.post(
                    ENDPOINT + "/input_tokens",
                    json={k: v for k, v in payload.items() if k in fields},
                    headers={"Authorization": "Bearer " + self.api_key.get_secret_value()},
                )
            if response.status_code != 200:
                raise ValueError("Token count unavailable")
            count = response.json().get("input_tokens")
            if type(count) is not int or count < 0:
                raise ValueError("Invalid token count")
            return count
        except (httpx.HTTPError, ValueError, AttributeError):
            from app.model_gateway import ModelUnavailable

            raise ModelUnavailable("Input token counting is temporarily unavailable.", "TokenCountUnavailable") from None

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
                searches = [
                    x
                    for x in body.get("output", [])
                    if isinstance(x, dict) and x.get("type") == "web_search_call"
                ]
                if searches and isinstance(exc.usage, dict):
                    if any(x.get("status") != "completed" and (x.get("action") or {}).get("type") not in {"open_page", "find_in_page"} for x in searches):
                        exc.usage = None
                    else:
                        exc.usage = exc.usage | {"web_search_calls": sum((x.get("action") or {}).get("type") == "search" for x in searches)}
            raise
