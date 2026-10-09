from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from market_intelligence.collectors.noaa import normalize_noaa_alert
from market_intelligence.data.connectors.inventory import InventoryConnector
from market_intelligence.data.connectors.routes import RouteConnector
from market_intelligence.data.connectors.sourcing import SourcingConnector
from market_intelligence.data.connectors.staffing import StaffingConnector
from market_intelligence.data.connectors.stores import StoreConnector
from market_intelligence.data.repositories import ConnectedOperationalRepository, InternalDataUnavailable
from market_intelligence.history.event_repository import ObservedEventHistory
from market_intelligence.history.uplift import aggregate_observed_uplift
from market_intelligence.weather.geography import match_alert_to_stores
from market_intelligence.weather.impact import ProductRequirement, evaluate_weather_impact


NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def _store_payload() -> dict:
    return {
        "store_id": "108",
        "name": "Store 108 - El Paso, TX",
        "state": "TX",
        "county_fips": "48141",
        "latitude": 31.7619,
        "longitude": -106.485,
    }


def _repository(*, stale: bool = False) -> ConnectedOperationalRepository:
    captured = NOW - timedelta(hours=30 if stale else 2)
    return ConnectedOperationalRepository(
        stores=StoreConnector(lambda _: [_store_payload()]),
        sourcing=SourcingConnector(
            lambda _: [
                {"store_id": "108", "sku": "WATER", "dc_id": "DC-1", "rank": 1, "transit_hours": 2, "service_level": "primary"},
                {"store_id": "108", "sku": "BATTERY", "dc_id": "DC-2", "rank": 1, "transit_hours": 3, "service_level": "primary"},
            ]
        ),
        inventory=InventoryConnector(
            lambda _: [
                {"location_id": "DC-1", "sku": "WATER", "on_hand": 24, "committed": 0, "safety_stock": 0, "eligible_inbound_before_cutoff": 0, "atp": 24, "captured_at": captured},
                {"location_id": "DC-2", "sku": "BATTERY", "on_hand": 12, "committed": 0, "safety_stock": 0, "eligible_inbound_before_cutoff": 0, "atp": 12, "captured_at": captured},
            ],
            max_age_hours=24,
        ),
        routes=RouteConnector(
            lambda _: [
                {"dc_id": "DC-1", "store_id": "108", "eta_hours": 2, "eligible": True},
                {"dc_id": "DC-2", "store_id": "108", "eta_hours": 3, "eligible": True},
            ]
        ),
        staffing=StaffingConnector(
            lambda start, end: [
                {"store_id": "108", "employee_token": "EMP-1", "shift_start": start, "shift_end": start + timedelta(hours=8), "scheduled_hours": 8}
            ]
        ),
    )


def _alert(*, geometry=True):
    return normalize_noaa_alert(
        {
            "id": "NWS-LIVE-1",
            "geometry": (
                {"type": "Polygon", "coordinates": [[[-107, 31], [-106, 31], [-106, 32], [-107, 32], [-107, 31]]]}
                if geometry else None
            ),
            "properties": {
                "event": "Flood Watch",
                "areaDesc": "El Paso County, TX",
                "headline": "Live Flood Watch",
                "onset": "2026-09-23T18:00:00+00:00",
                "ends": "2026-09-24T06:00:00+00:00",
                "geocode": {"SAME": ["048141"]},
            },
        },
        state="TX",
    )


def test_live_noaa_and_connected_internal_data_produce_traceable_multi_sku_allocations() -> None:
    repository = _repository()
    stores = [store.model_dump() for store in repository.get_stores(NOW)]
    inventory = {(row.location_id, row.sku): row for row in repository.get_inventory(NOW)}
    result = evaluate_weather_impact(
        alert=_alert(),
        stores=stores,
        requirements=[
            ProductRequirement("108", "WATER", 22.6, 6, 0, {"dc_id": "DC-1", "dc_name": "Dallas DC", "atp": inventory[("DC-1", "WATER")].atp, "eligible": True}, []),
            ProductRequirement("108", "BATTERY", 11.1, 12, 0, {"dc_id": "DC-2", "dc_name": "Fort Worth DC", "atp": inventory[("DC-2", "BATTERY")].atp, "eligible": True}, []),
        ],
        evaluated_at=NOW,
    )

    assert result.geography.method == "NOAA polygon point intersection"
    assert [row.primary_dc_id for row in result.allocations] == ["DC-1", "DC-2"]
    assert [row.required_quantity for row in result.allocations] == [24, 12]
    assert all(row.remaining_gap == 0 for row in result.allocations)


def test_unavailable_or_stale_internals_fail_before_recommendations() -> None:
    with pytest.raises(InternalDataUnavailable, match="stale snapshot"):
        _repository(stale=True).get_inventory(NOW)

    no_match = evaluate_weather_impact(
        alert=_alert(geometry=False),
        stores=[{**_store_payload(), "county_fips": "48001", "county": "Anderson County"}],
        requirements=[ProductRequirement("108", "WATER", 20, 1, 0, {}, [])],
        evaluated_at=NOW,
    )
    assert no_match.allocations == ()


def test_fips_fallback_and_observed_history_insufficiency_remain_explicit() -> None:
    mapping = match_alert_to_stores(
        "Broad TX area",
        "TX",
        [_store_payload()],
        alert_geocodes={"SAME": ["048141"]},
    )
    assert mapping.method == "NOAA UGC/FIPS join"

    event = ObservedEventHistory(
        event_id="OBS-1", noaa_source_id="NWS-OLD-1", event_type="Flood Watch",
        hazard_family="flood", geography_fips=("48141",), severity="Moderate", season="summer",
        category="Emergency Essentials", baseline_start=NOW - timedelta(days=30),
        baseline_end=NOW - timedelta(days=2), event_start=NOW - timedelta(days=1), event_end=NOW,
        baseline_units=(100, 100, 100, 100, 100, 100, 100), event_units=(120,),
        observed_uplift_pct=20, traffic_change_pct=-10, data_coverage=0.95,
        sample_size=8, source_system="POS + NOAA",
    )
    result = aggregate_observed_uplift([event])
    assert not result.quantitative
    assert result.uplift_pct is None
    assert "3 required" in result.reason
