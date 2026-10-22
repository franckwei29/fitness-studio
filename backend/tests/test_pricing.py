from decimal import Decimal

import pytest

from app.pricing import FLASH_LITE, UsageUncertain, price_for


def usage(**changes):
    return (
        dict(
            total_input_tokens=1000,
            total_output_tokens=100,
            total_thought_tokens=50,
            total_cached_tokens=0,
            total_tool_use_tokens=0,
            total_tokens=1150,
        )
        | changes
    )


def test_thinking_tokens_are_included_and_cost_rounds_up():
    assert FLASH_LITE.actual(usage()) == 675
    assert FLASH_LITE.cost(1, 1) == 3
    assert FLASH_LITE.reservation() >= FLASH_LITE.actual(usage())
    assert FLASH_LITE.input_per_million == Decimal("0.30")


@pytest.mark.parametrize(
    "value",
    [
        {},
        usage(total_tokens=1000),
        usage(total_input_tokens=True),
        usage(total_cached_tokens=20),
        usage(total_tool_use_tokens=10),
    ],
)
def test_missing_or_unsupported_usage_is_not_zero(value):
    with pytest.raises(UsageUncertain):
        FLASH_LITE.actual(value)


def test_unknown_model_has_no_implicit_price():
    with pytest.raises(UsageUncertain):
        price_for("another-model")
