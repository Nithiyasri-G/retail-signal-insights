from decimal import Decimal

from market_intelligence.replenishment.quantity import calculate_order_quantity


def test_fractional_forecast_shortfall_rounds_to_sellable_unit() -> None:
    result = calculate_order_quantity(Decimal("22.6"), case_pack=1, moq=0)

    assert result.forecast_shortfall == Decimal("22.6")
    assert result.order_quantity == 23
    assert result.rounding_reason == "Rounded up to the next sellable unit."


def test_order_quantity_rounds_up_to_complete_case_pack() -> None:
    result = calculate_order_quantity(Decimal("22.6"), case_pack=6, moq=0)

    assert result.order_quantity == 24
    assert result.case_rounding_applied is True
    assert result.moq_applied is False
    assert "6 units per case" in result.rounding_reason
    assert "case pack of 6" not in result.rounding_reason


def test_moq_is_applied_before_case_pack_rounding() -> None:
    result = calculate_order_quantity(Decimal("22.6"), case_pack=6, moq=30)

    assert result.order_quantity == 30
    assert result.moq_applied is True
    assert "MOQ of 30" in result.rounding_reason


def test_case_rounding_reason_is_short_and_does_not_repeat_the_gap() -> None:
    """The Gap is already its own column; the reason should state the rule, not repeat it."""
    result = calculate_order_quantity(Decimal("22.6"), case_pack=6, moq=0)

    assert result.rounding_reason == "Rounded up to a complete case (6 units per case); final order equals 4 cases of 6 units each."


def test_review_example_reconciles_gap_case_and_moq() -> None:
    """Matches the review's worked example: 2.4 unit gap, 6/case, MOQ 12 -> 12 units / 2 cases."""
    result = calculate_order_quantity(Decimal("2.4"), case_pack=6, moq=12)

    assert result.case_rounded_requirement == 6
    assert result.moq_applied is True
    assert result.order_quantity == 12
    assert "MOQ of 12 units applied" in result.rounding_reason
    assert "2 cases of 6 units each" in result.rounding_reason


def test_zero_shortfall_does_not_create_an_order() -> None:
    result = calculate_order_quantity(Decimal("0"), case_pack=12, moq=24)

    assert result.order_quantity == 0
    assert result.rounding_reason == "No forecast shortfall; no order required."
