"""OpenAI wire contracts and accounting, with no external requests."""

import copy
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.agent_contracts import ChatReply, IntakeProposal
from app.agent_versions import verify_version, version_fingerprint
from app.gemini import ProviderFailure
from app.model_payload import build_payload as version_payload
from app.openai_provider import OpenAITransport, build_payload, decode_response
from app.policy import PolicyDenied
from app.pricing import LUNA, UsageUncertain


def usage(**changes):
    return {
        "input_tokens": 1000,
        "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 0},
        "output_tokens": 100,
        "output_tokens_details": {"reasoning_tokens": 40},
        "total_tokens": 1100,
    } | changes


def response():
    return {
        "id": "resp_synthetic",
        "status": "completed",
        "usage": usage(),
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": '{"reply":"Welcome"}'}],
            },
        ],
    }


def test_bilingual_nonstored_structured_request_and_decoding():
    payload = build_payload("gpt-6-luna", "Reply in English.", {"message": "你好"}, IntakeProposal)
    assert payload["store"] is False and payload["stream"] is False
    assert payload["service_tier"] == "default"
    assert payload["reasoning"] == {"effort": "low"} and "你好" in payload["input"]
    schema = payload["text"]["format"]["schema"]["$defs"]["IntakeFacts"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert {"type": "null"} in schema["properties"]["age"]["anyOf"]
    assert decode_response(response(), ChatReply).output.reply == "Welcome"


@pytest.mark.parametrize(
    "body",
    [
        response() | {"status": "incomplete"},
        response() | {"usage": None},
        response() | {"output": [{"type": "function_call", "name": "unauthorized"}]},
        response()
        | {
            "output": [
                {"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "No"}]}
            ]
        },
    ],
)
def test_incomplete_refused_and_tool_responses_are_not_published(body):
    with pytest.raises(ProviderFailure):
        decode_response(body, ChatReply)


def test_failure_uses_one_request_and_redacts_credentials_and_provider_details():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-private-key"
        return httpx.Response(429, json={"error": "private account details"})

    with pytest.raises(ProviderFailure) as error:
        OpenAITransport(SecretStr("synthetic-private-key"), httpx.MockTransport(handler)).send(
            build_payload("gpt-6-luna", "Reply.", {}, ChatReply), ChatReply
        )
    assert len(calls) == 1
    assert "synthetic-private-key" not in str(error.value) and "private account" not in str(error.value)


def test_cached_and_reasoning_tokens_are_not_double_counted():
    assert LUNA.actual(usage()) == 132
    assert LUNA.actual(usage(input_tokens_details={"cached_tokens": 0, "cache_write_tokens": 0})) == 150
    payload = build_payload("gpt-6-luna", "Reply.", {}, ChatReply)
    assert LUNA.reservation_for(payload) > LUNA.actual(usage())
    assert LUNA.reservation_for(payload) < LUNA.reservation()


@pytest.mark.parametrize(
    "value",
    [
        {},
        usage(total_tokens=1101),
        usage(input_tokens=True),
        usage(input_tokens_details={"cached_tokens": 1001}),
        usage(output_tokens_details={"reasoning_tokens": 101}),
        usage(output_tokens=4097, total_tokens=5097),
    ],
)
def test_invalid_accounting_never_becomes_a_zero_charge(value):
    with pytest.raises(UsageUncertain):
        LUNA.actual(value)


def test_options_are_pinned_and_mismatch_or_tampering_is_rejected():
    row = SimpleNamespace(
        role="Coach",
        model="gpt-6-luna",
        provider="openai",
        prompt="Reply.",
        max_output_tokens=512,
        reasoning_effort="none",
    )
    row.fingerprint = version_fingerprint(row.role, row.model, row.prompt, 512, "none")
    assert verify_version(row, "Coach") is row
    payload = version_payload(row.model, row.prompt, {}, ChatReply, 4096, version=row)
    assert payload["max_output_tokens"] == 512 and payload["reasoning"]["effort"] == "none"
    changed = copy.copy(row)
    changed.max_output_tokens = 1024
    with pytest.raises(PolicyDenied):
        verify_version(changed, "Coach")
    row.provider = "gemini"
    with pytest.raises(PolicyDenied):
        verify_version(row, "Coach")


def test_default_cache_writes_use_the_write_rate_and_cannot_overlap_reads():
    assert LUNA.actual(usage(input_tokens_details={"cached_tokens": 200, "cache_write_tokens": 600})) == 147
    with pytest.raises(UsageUncertain):
        LUNA.actual(usage(input_tokens_details={"cached_tokens": 200, "cache_write_tokens": 900}))
