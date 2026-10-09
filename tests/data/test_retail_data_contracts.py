from __future__ import annotations

from datetime import datetime, timezone

from market_intelligence.data.fixture_repository import FixtureRepository
from market_intelligence.models.inventory import InventorySnapshot
from market_intelligence.replenishment.allocation import allocate_product_requirement


NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def _dc(dc_id: str, name: str, atp: float, captured_at: str) -> dict:
    return {
        "dc_id": dc_id,
        "dc_name": name,
        "atp": atp,
        "eta_hours": 2,
        "eligible": True,
        "captured_at": captured_at,
    }


def test_store_geo_sourcing_rank_and_pack_contracts() -> None:
    repository = FixtureRepository("v1")
    stores = repository.table("stores")
    lanes = repository.table("store_sku_dc_lanes")
    products = repository.table("products")

    assert stores["FIPS"].str.fullmatch(r"\d{5}").all()
    assert stores["Latitude"].between(-90, 90).all()
    assert stores["Longitude"].between(-180, 180).all()
    assert not lanes.duplicated(["Store ID", "UPC", "Rank"]).any()
    assert ((products["MOQ"] == 0) | (products["MOQ"] % products["Case Pack"] == 0)).all()


def test_atp_components_and_inventory_freshness() -> None:
    snapshot = InventorySnapshot(
        location_id="DC-1",
        sku="WATER",
        on_hand=100,
        committed=25,
        safety_stock=10,
        eligible_inbound_before_cutoff=20,
        atp=85,
        captured_at=datetime(2026, 9, 23, 8, tzinfo=timezone.utc),
    )

    assert snapshot.atp == 100 - 25 - 10 + 20
    assert snapshot.is_fresh(at=NOW, max_age_hours=6)
    assert not snapshot.is_fresh(at=NOW, max_age_hours=3)


def test_stale_inventory_is_rejected_for_operational_allocation() -> None:
    allocation = allocate_product_requirement(
        store_id="108",
        sku="WATER",
        required_quantity=24,
        primary=_dc("DC-1", "Dallas DC", 100, "2026-09-20T00:00:00+00:00"),
        alternates=[_dc("DC-2", "Fort Worth DC", 100, "2026-09-20T00:00:00+00:00")],
        decision_time=NOW,
        max_inventory_age_hours=24,
    )

    assert allocation.primary_supply == 0
    assert allocation.backup_supply == 0
    assert allocation.remaining_gap == 24


def test_different_skus_can_use_different_primary_dcs() -> None:
    fresh = "2026-09-23T08:00:00+00:00"
    water = allocate_product_requirement(
        store_id="108",
        sku="WATER",
        required_quantity=24,
        primary=_dc("DC-1", "Dallas DC", 24, fresh),
        alternates=[],
        decision_time=NOW,
    )
    batteries = allocate_product_requirement(
        store_id="108",
        sku="BATTERY",
        required_quantity=12,
        primary=_dc("DC-2", "Fort Worth DC", 12, fresh),
        alternates=[],
        decision_time=NOW,
    )

    assert water.primary_dc_name == "Dallas DC"
    assert batteries.primary_dc_name == "Fort Worth DC"
    assert water.primary_supply == 24
    assert batteries.primary_supply == 12
