"""Transport contracts use synthetic responses and never reach Google."""

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr

from app.gemini import GeminiTransport, ProviderFailure, build_payload, decode_response


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str


def response():
    return {
        "id": "test-provider-id",
        "status": "completed",
        "usage": {"total_input_tokens": 10},
        "steps": [
            {"type": "thought", "summary": []},
            {"type": "model_output", "content": [{"type": "text", "text": '{"reply":"Welcome"}'}]},
        ],
    }


def test_steps_output_and_nonstored_bilingual_input():
    payload = build_payload("test-model", "Reply in English.", {"message": "你好"}, Reply)
    assert "你好" in payload["input"]
    assert payload["store"] is False
    assert decode_response(response(), Reply).output.reply == "Welcome"


@pytest.mark.parametrize(
    "body",
    [
        {"status": "completed", "outputs": [{"text": "Legacy"}]},
        response() | {"status": "in_progress"},
        response() | {"steps": [{"type": "function_call", "name": "unauthorized"}]},
        response() | {"usage": None},
    ],
)
def test_invalid_or_incomplete_output_is_not_success(body):
    with pytest.raises(ProviderFailure):
        decode_response(body, Reply)


def test_failure_is_single_attempt_and_does_not_expose_key():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"error": "Private provider details"})

    transport = GeminiTransport(SecretStr("test-secret"), httpx.MockTransport(handler))
    with pytest.raises(ProviderFailure) as caught:
        transport.send(build_payload("test-model", "Reply in English.", {}, Reply), Reply)
    assert len(calls) == 1
    assert "test-secret" not in str(caught.value)
    assert "Private provider details" not in str(caught.value)


def test_nullable_schema_normalization_preserves_local_contract():
    from app.agent_contracts import IntakeProposal

    payload = build_payload("test-model", "Extract confirmed facts.", {}, IntakeProposal)
    age = payload["response_format"]["schema"]["$defs"]["IntakeFacts"]["properties"]["age"]
    assert age["type"] == ["integer", "null"]
    assert "anyOf" not in age
    assert age["minimum"] == 1
    assert age["maximum"] == 120


def test_completed_nonstored_response_may_omit_provider_id():
    body = response()
    body.pop("id")
    result = decode_response(body, Reply)
    assert result.provider_id is None
    assert result.output.reply == "Welcome"
    assert result.usage == body["usage"]


def test_provider_schema_requires_every_nullable_fact_to_be_returned():
    from app.agent_contracts import IntakeProposal

    payload = build_payload("test-model", "Extract facts.", {}, IntakeProposal)
    fields = payload["response_format"]["schema"]["$defs"]["IntakeFacts"]
    assert set(fields["required"]) == set(fields["properties"])
    assert "default" not in fields["properties"]["available_days"]
    assert fields["properties"]["available_days"]["type"] == ["integer", "null"]
