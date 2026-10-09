from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class AllocationRow:
    store_id: str
    sku: str
    required_quantity: float
    primary_dc_id: str
    primary_dc_name: str
    primary_atp: float
    primary_supply: float
    backup_dc_id: str
    backup_dc_name: str
    backup_atp: float
    backup_supply: float
    remaining_gap: float
    allocation_reason: str


def _number(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _eligible(
    candidate: Mapping[str, Any],
    *,
    decision_time: datetime | None,
    max_inventory_age_hours: float,
) -> bool:
    if not bool(candidate.get("eligible", True)) or _number(candidate.get("atp")) <= 0:
        return False
    if decision_time is None:
        return True
    captured_raw = candidate.get("captured_at")
    if not captured_raw:
        return False
    captured = captured_raw if isinstance(captured_raw, datetime) else datetime.fromisoformat(str(captured_raw).replace("Z", "+00:00"))
    if captured.tzinfo is None or decision_time.tzinfo is None:
        return False
    age = decision_time - captured
    return timedelta(0) <= age <= timedelta(hours=max_inventory_age_hours)


def allocate_product_requirement(
    *,
    store_id: str,
    sku: str,
    required_quantity: float,
    primary: Mapping[str, Any],
    alternates: Iterable[Mapping[str, Any]],
    decision_time: datetime | None = None,
    max_inventory_age_hours: float = 24.0,
) -> AllocationRow:
    """Allocate one Store/SKU requirement while minimizing split shipments."""
    required = _number(required_quantity)
    primary_atp = _number(primary.get("atp"))
    primary_eligible = _eligible(
        primary,
        decision_time=decision_time,
        max_inventory_age_hours=max_inventory_age_hours,
    )
    candidates = sorted(
        (
            candidate
            for candidate in alternates
            if _eligible(
                candidate,
                decision_time=decision_time,
                max_inventory_age_hours=max_inventory_age_hours,
            )
        ),
        key=lambda candidate: (
            _number(candidate.get("eta_hours")),
            -_number(candidate.get("atp")),
            str(candidate.get("dc_id") or ""),
        ),
    )

    selected: Mapping[str, Any] = candidates[0] if candidates else {}
    primary_supply = 0.0
    backup_supply = 0.0
    reason = "No executable requirement."

    if required > 0 and primary_eligible and primary_atp >= required:
        primary_supply = required
        # No backup DC was actually consulted for supply -- report none, rather than
        # naming an alternate DC (with its unrelated ATP) that supplied zero units.
        selected = {}
        reason = "Primary DC supplies the complete requirement."
    elif required > 0:
        full_alternate = next(
            (candidate for candidate in candidates if _number(candidate.get("atp")) >= required),
            None,
        )
        if full_alternate is not None:
            selected = full_alternate
            backup_supply = required
            reason = "One eligible alternate supplies the complete requirement; no split shipment."
        else:
            # Ajith review rule: do NOT split one Store/SKU order across DCs.
            # If the primary DC cannot cover the complete order quantity, and no
            # single alternate can cover the complete order quantity, keep the full
            # requirement as residual gap for planner escalation. Do not recommend
            # partial movement from primary + partial movement from backup.
            primary_supply = 0.0
            backup_supply = 0.0
            if primary_eligible and primary_atp > 0 and selected:
                reason = "No single DC can cover the complete requirement; no split shipment, escalate residual gap."
            elif primary_eligible and primary_atp > 0:
                reason = "Primary DC has partial ATP only; no split shipment, escalate residual gap."
            elif selected:
                reason = "Alternate DC has partial ATP only; no split shipment, escalate residual gap."
            else:
                reason = "No eligible DC can supply the complete requirement; escalate residual gap."

    remaining_gap = max(0.0, required - primary_supply - backup_supply)
    return AllocationRow(
        store_id=str(store_id),
        sku=str(sku),
        required_quantity=round(required, 3),
        primary_dc_id=str(primary.get("dc_id") or ""),
        primary_dc_name=str(primary.get("dc_name") or "Unassigned DC"),
        primary_atp=round(primary_atp, 3),
        primary_supply=round(primary_supply, 3),
        backup_dc_id=str(selected.get("dc_id") or ""),
        backup_dc_name=str(selected.get("dc_name") or "No eligible alternate"),
        backup_atp=round(_number(selected.get("atp")), 3),
        backup_supply=round(backup_supply, 3),
        remaining_gap=round(remaining_gap, 3),
        allocation_reason=reason,
    )


def summarize_dc_allocations(rows: Iterable[AllocationRow]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for row in rows:
        if row.primary_supply > 0:
            totals[row.primary_dc_name] = round(
                totals.get(row.primary_dc_name, 0.0) + row.primary_supply,
                3,
            )
        if row.backup_supply > 0:
            totals[row.backup_dc_name] = round(
                totals.get(row.backup_dc_name, 0.0) + row.backup_supply,
                3,
            )
    return totals
