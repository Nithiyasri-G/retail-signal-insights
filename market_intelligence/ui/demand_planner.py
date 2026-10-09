from __future__ import annotations


def quantity_column_help() -> dict[str, str]:
    return {
        "Forecast Shortfall": "Projected demand minus Store on-hand and inbound; may be fractional.",
        "Order Quantity": "Executable whole units after sellable-unit, case-pack, and MOQ rounding.",
    }
