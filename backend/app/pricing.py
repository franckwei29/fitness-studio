"""Versioned text-only Gemini pricing in integer micro-US dollars.

Sources checked 2026-10-22:
https://ai.google.dev/gemini-api/docs/pricing
https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite
https://ai.google.dev/api/interactions-api
"""

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


def price_for(model):
    if model != FLASH_LITE.model:
        raise UsageUncertain("No verified price is configured for this model.")
    return FLASH_LITE
