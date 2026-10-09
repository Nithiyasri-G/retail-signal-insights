from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from market_intelligence.models.alerts import WeatherAlert
from market_intelligence.replenishment.allocation import AllocationRow, allocate_product_requirement
from market_intelligence.replenishment.quantity import calculate_order_quantity
from market_intelligence.weather.geography import GeographyMatch, match_alert_to_stores


@dataclass(frozen=True)
class ProductRequirement:
    store_id: str
    sku: str
    forecast_shortfall: float
    case_pack: int
    moq: int
    primary: Mapping[str, Any]
    alternates: Sequence[Mapping[str, Any]]


@dataclass(frozen=True)
class WeatherImpact:
    alert_id: str
    evaluated_at: datetime
    geography: GeographyMatch
    allocations: tuple[AllocationRow, ...]


def evaluate_weather_impact(
    *,
    alert: WeatherAlert,
    stores: Sequence[Mapping[str, Any]],
    requirements: Sequence[ProductRequirement],
    evaluated_at: datetime,
) -> WeatherImpact:
    geography = match_alert_to_stores(
        alert.area_description,
        alert.state,
        stores,
        alert_geometry=alert.geometry,
        alert_geocodes=alert.geocodes,
    )
    affected = set(geography.affected_store_ids)
    allocations: list[AllocationRow] = []
    for requirement in requirements:
        if requirement.store_id not in affected:
            continue
        order = calculate_order_quantity(
            requirement.forecast_shortfall,
            case_pack=requirement.case_pack,
            moq=requirement.moq,
        )
        allocations.append(
            allocate_product_requirement(
                store_id=requirement.store_id,
                sku=requirement.sku,
                required_quantity=order.order_quantity,
                primary=requirement.primary,
                alternates=requirement.alternates,
            )
        )
    return WeatherImpact(alert.alert_id, evaluated_at, geography, tuple(allocations))
