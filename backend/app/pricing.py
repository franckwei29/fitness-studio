"""Versioned standard text pricing in integer micro-US dollars.

Gemini sources checked 2026-10-22; OpenAI sources checked 2026-10-30:
https://developers.openai.com/api/docs/models/gpt-6-luna
https://developers.openai.com/api/docs/guides/prompt-caching
https://ai.google.dev/gemini-api/docs/pricing
https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite
https://ai.google.dev/api/interactions-api
"""

import json
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal


class UsageUncertain(Exception):
    """Accounting needs reconciliation rather than an invented zero charge."""


@dataclass(frozen=True)
class TextPrice:
    model: str
    version: str
    input_per_million: Decimal
    output_per_million: Decimal
    max_input_tokens: int
    max_output_tokens: int

    def cost(self, input_tokens, output_tokens):
        # USD per million tokens equals micro-USD per token.
        return int(
            (
                input_tokens * self.input_per_million + output_tokens * self.output_per_million
            ).to_integral_value(rounding=ROUND_CEILING)
        )

    def reservation(self):
        # Reserve the entire documented model capacity rather than pretending
        # that character counts establish a strict tokenizer upper bound.
        return self.cost(self.max_input_tokens, self.max_output_tokens)

    def reservation_for(self, payload):
        # Byte-level BPE cannot use more input tokens than bytes, plus a conservative
        # framing allowance. Gemini thought usage retains its full documented cap.
        input_bound = min(self.max_input_tokens, len(json.dumps(payload, ensure_ascii=False).encode()) + 4096)
        output_bound = payload.get("max_output_tokens", self.max_output_tokens)
        return max(1, self.cost(input_bound, output_bound))

    def actual(self, usage):
        fields = [
            "total_input_tokens",
            "total_output_tokens",
            "total_thought_tokens",
            "total_cached_tokens",
            "total_tool_use_tokens",
            "total_tokens",
        ]
        if any(type(usage.get(key)) is not int or usage[key] < 0 for key in fields):
            raise UsageUncertain("Token accounting is incomplete or invalid.")
        if usage["total_cached_tokens"] or usage["total_tool_use_tokens"]:
            raise UsageUncertain("This usage requires a different pricing calculation.")
        inputs = usage["total_input_tokens"]
        outputs = usage["total_output_tokens"] + usage["total_thought_tokens"]
        if inputs + outputs != usage["total_tokens"]:
            raise UsageUncertain("Token accounting does not reconcile.")
        if inputs > self.max_input_tokens or outputs > self.max_output_tokens:
            raise UsageUncertain("Usage exceeds the documented pricing bounds.")
        return self.cost(inputs, outputs)


FLASH_LITE = TextPrice(
    "gemini-3.5-flash-lite",
    "google-text-standard-2026-10-22",
    Decimal("0.30"),
    Decimal("2.50"),
    1048576,
    65536,
)


@dataclass(frozen=True)
class OpenAITextPrice(TextPrice):
    cached_per_million: Decimal = Decimal("0.01")
    cache_write_per_million: Decimal = Decimal("0.125")

    def reservation_for(self, payload):
        input_bound = min(self.max_input_tokens, len(json.dumps(payload, ensure_ascii=False).encode()) + 4096)
        return max(
            1,
            int(
                (
                    input_bound * self.cache_write_per_million
                    + payload["max_output_tokens"] * self.output_per_million
                ).to_integral_value(rounding=ROUND_CEILING)
            ),
        )

    def actual(self, usage):
        keys = ("input_tokens", "output_tokens", "total_tokens")
        if any(type(usage.get(key)) is not int or usage[key] < 0 for key in keys):
            raise UsageUncertain("Token accounting is incomplete or invalid.")
        inputs, outputs = usage["input_tokens"], usage["output_tokens"]
        details = usage.get("input_tokens_details")
        output_details = usage.get("output_tokens_details")
        if not isinstance(details, dict) or not isinstance(output_details, dict):
            raise UsageUncertain("Detailed token accounting is missing.")
        cached, reasoning = details.get("cached_tokens"), output_details.get("reasoning_tokens")
        writes = details.get("cache_write_tokens")
        if (
            type(cached) is not int
            or not 0 <= cached <= inputs
            or type(reasoning) is not int
            or not 0 <= reasoning <= outputs
        ):
            raise UsageUncertain("Detailed token accounting is invalid.")
        if type(writes) is not int or not 0 <= writes <= inputs - cached:
            raise UsageUncertain("Cache-write token accounting is invalid.")
        lifetimes = details.get("cache_write_tokens_details")
        if lifetimes is not None and (
            not isinstance(lifetimes, dict)
            or type(lifetimes.get("ttl_30m_tokens")) is not int
            or lifetimes.get("ttl_30m_tokens") != writes
            or type(lifetimes.get("ttl_12h_tokens")) is not int
            or lifetimes.get("ttl_12h_tokens") != 0
        ):
            raise UsageUncertain("Cache lifetime does not match the supported 30-minute rate.")
        if inputs + outputs != usage["total_tokens"] or inputs > 272000 or outputs > self.max_output_tokens:
            raise UsageUncertain("Usage exceeds the supported standard text pricing bounds.")
        # output_tokens already includes reasoning; never add it a second time.
        return int(
            (
                (inputs - cached - writes) * self.input_per_million
                + cached * self.cached_per_million
                + writes * self.cache_write_per_million
                + outputs * self.output_per_million
            ).to_integral_value(rounding=ROUND_CEILING)
        )


LUNA = OpenAITextPrice(
    "gpt-6-luna", "openai-text-standard-cachewrite-2026-10-30", Decimal("0.10"), Decimal("0.50"), 272000, 4096
)


def price_for(model):
    for price in (FLASH_LITE, LUNA):
        if model == price.model:
            return price
    raise UsageUncertain("No verified price is configured for this model.")
