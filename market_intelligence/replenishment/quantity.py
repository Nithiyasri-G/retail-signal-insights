from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from typing import Union


Number = Union[int, float, str, Decimal]


@dataclass(frozen=True)
class OrderQuantity:
    forecast_shortfall: Decimal
    order_quantity: int
    case_pack: int
    moq: int
    case_rounded_requirement: int
    moq_applied: bool
    case_rounding_applied: bool
    rounding_reason: str


def _decimal(value: Number) -> Decimal:
    return Decimal(str(value))


def _round_up_to_pack(value: Decimal, pack: int) -> int:
    packs = (value / Decimal(pack)).to_integral_value(rounding=ROUND_CEILING)
    return int(packs * pack)


def calculate_order_quantity(
    forecast_shortfall: Number,
    *,
    case_pack: int = 1,
    moq: int = 0,
) -> OrderQuantity:
    """Convert a forecast shortage into an executable, auditable order quantity.

    Two rules apply in a fixed order, each shown separately so the displayed
    explanation reconciles exactly to the Order Quantity:
      1. The Inventory Gap is rounded up to the next complete case (units per case).
      2. That case-rounded requirement is compared with the Minimum Order Quantity
         (also rounded up to a complete case), and the larger valid quantity is used.
    """
    shortfall = max(Decimal("0"), _decimal(forecast_shortfall))
    pack = max(1, int(case_pack))
    minimum = max(0, int(moq))
    if shortfall == 0:
        return OrderQuantity(
            shortfall, 0, pack, minimum, 0, False, False,
            "No forecast shortfall; no order required.",
        )

    case_rounded = _round_up_to_pack(shortfall, pack)
    moq_applied = minimum > case_rounded
    quantity = _round_up_to_pack(Decimal(minimum), pack) if moq_applied else case_rounded
    case_rounding_applied = pack > 1 and case_rounded != shortfall
    cases = quantity // pack

    def _case_phrase(count: int) -> str:
        return f"{count} case{'s' if count != 1 else ''} of {pack} unit{'s' if pack != 1 else ''} each"

    if moq_applied:
        reason = f"MOQ of {minimum} units applied; final order equals {_case_phrase(cases)}."
    elif case_rounding_applied:
        reason = f"Rounded up to a complete case ({pack} units per case); final order equals {_case_phrase(cases)}."
    elif pack > 1:
        reason = f"Already a whole number of cases; final order equals {_case_phrase(cases)}."
    elif shortfall != shortfall.to_integral_value():
        reason = "Rounded up to the next sellable unit."
    else:
        reason = "No rounding required."

    return OrderQuantity(
        shortfall, quantity, pack, minimum, case_rounded, moq_applied, case_rounding_applied, reason,
    )
