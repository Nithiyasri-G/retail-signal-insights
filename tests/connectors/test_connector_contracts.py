from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from market_intelligence.data.connectors.inventory import InventoryConnector
from market_intelligence.data.connectors.products import ProductConnector
from market_intelligence.data.connectors.routes import RouteConnector
from market_intelligence.data.connectors.sourcing import SourcingConnector
from market_intelligence.data.connectors.staffing import StaffingConnector
from market_intelligence.data.connectors.stores import StoreConnector
from market_intelligence.data.repositories import (
    ConnectedOperationalRepository,
    FixtureOperationalRepository,
    InternalDataUnavailable,
    require_operational_repository,
)


NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def _connected_repository(*, stale: bool = False) -> ConnectedOperationalRepository:
    captured = NOW - timedelta(hours=30 if stale else 2)
    return ConnectedOperationalRepository(
        stores=StoreConnector(
            lambda _: [
                {
                    "store_id": "108",
                    "name": "Store 108 - El Paso, TX",
                    "state": "TX",
                    "county_fips": "48141",
                    "latitude": 31.7619,
                    "longitude": -106.485,
                }
            ]
        ),
        sourcing=SourcingConnector(
            lambda _: [
                {
                    "store_id": "108",
                    "sku": "049000028911",
                    "dc_id": "DC-TX1",
                    "rank": 1,
                    "transit_hours": 4,
                    "service_level": "primary",
                    "active": True,
                }
            ]
        ),
        inventory=InventoryConnector(
            lambda _: [
                {
                    "location_id": "DC-TX1",
                    "sku": "049000028911",
                    "on_hand": 100,
                    "committed": 20,
                    "safety_stock": 10,
                    "eligible_inbound_before_cutoff": 5,
                    "atp": 75,
                    "captured_at": captured,
                }
            ],
            max_age_hours=24,
        ),
        routes=RouteConnector(
            lambda _: [
                {
                    "dc_id": "DC-TX1",
                    "store_id": "108",
                    "eta_hours": 4,
                    "eligible": True,
                    "route_counties": ["Dallas County", "El Paso County"],
                }
            ]
        ),
        staffing=StaffingConnector(
            lambda start, end: [
                {
                    "store_id": "108",
                    "employee_token": "EMP-108-01",
                    "shift_start": start,
                    "shift_end": start + timedelta(hours=8),
                    "scheduled_hours": 8,
                    "on_call_eligible": False,
                }
            ]
        ),
    )


def test_connected_and_fixture_adapters_return_typed_contracts() -> None:
    connected = _connected_repository()
    fixture = FixtureOperationalRepository("v1")

    assert connected.get_stores(NOW)[0].store_id == "108"
    assert fixture.get_stores(NOW)[-1].store_id == "208"
    assert connected.get_store_sku_lanes(NOW)[0].rank == 1
    assert connected.get_inventory(NOW)[0].atp == 75
    assert connected.get_routes(NOW)[0].eligible
    assert connected.get_staffing_window(NOW, NOW + timedelta(hours=24))[0].scheduled_hours == 8
    assert fixture.get_inventory(NOW)
    assert fixture.get_routes(NOW)
    assert fixture.get_staffing_window(NOW, NOW + timedelta(hours=48))


def test_real_connector_schema_drift_is_visible() -> None:
    connector = StoreConnector(
        lambda _: [
            {
                "store_id": "108",
                "name": "El Paso",
                "state": "TX",
                "county_fips": "48141",
                "latitude": 31.7,
                "longitude": -106.4,
                "unexpected_field": "schema drift",
            }
        ]
    )

    with pytest.raises(Exception, match="unexpected_field"):
        connector.get_stores(NOW)


def test_stale_real_inventory_fails_closed_without_fixture_fallback() -> None:
    repository = _connected_repository(stale=True)

    with pytest.raises(InternalDataUnavailable, match="Internal operational data unavailable: inventory"):
        repository.get_inventory(NOW)


def test_connected_mode_without_connectors_fails_closed() -> None:
    with pytest.raises(InternalDataUnavailable, match="connectors are not configured"):
        require_operational_repository(mode="connected")


def test_product_connector_and_duplicate_completeness_gates() -> None:
    product = {
        "sku": "WATER", "name": "Water", "category": "Emergency",
        "case_pack": 6, "moq": 12, "sellable_unit": "EA",
    }
    assert ProductConnector(lambda _: [product]).get_products(NOW)[0].sku == "WATER"
    with pytest.raises(ValueError, match="duplicate SKUs"):
        ProductConnector(lambda _: [product, product]).get_products(NOW)


def test_empty_and_out_of_window_connector_results_are_visible() -> None:
    with pytest.raises(ValueError, match="no records"):
        StoreConnector(lambda _: []).get_stores(NOW)
    with pytest.raises(ValueError, match="outside requested window"):
        StaffingConnector(
            lambda start, end: [
                {
                    "store_id": "108", "employee_token": "EMP-1",
                    "shift_start": start - timedelta(hours=1), "shift_end": start + timedelta(hours=7),
                    "scheduled_hours": 8,
                }
            ]
        ).get_staffing_window(NOW, NOW + timedelta(hours=24))
