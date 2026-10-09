from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from market_intelligence.models.alerts import WeatherAlert
from market_intelligence.models.incidents import DataMode, Incident, ProvenanceRecord
from market_intelligence.models.inventory import InventorySnapshot
from market_intelligence.models.products import Product
from market_intelligence.models.stores import Store, StoreSkuSourcingLane


NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def test_invalid_state_and_fips_are_rejected() -> None:
    with pytest.raises(ValidationError) as error:
        Store(store_id="108", name="El Paso", state="Texas", county_fips="48", latitude=31.7, longitude=-106.4)

    assert {item["loc"][0] for item in error.value.errors()} == {"state", "county_fips"}


def test_negative_atp_nonpositive_case_pack_and_naive_timestamp_are_rejected() -> None:
    with pytest.raises(ValidationError) as inventory_error:
        InventorySnapshot(
            location_id="DC-1",
            sku="WATER",
            on_hand=10,
            committed=12,
            safety_stock=0,
            eligible_inbound_before_cutoff=0,
            atp=-2,
            captured_at=datetime(2026, 9, 23, 12),
        )
    with pytest.raises(ValidationError):
        Product(sku="WATER", name="Water", category="Emergency", case_pack=0, moq=0)

    fields = {item["loc"][0] for item in inventory_error.value.errors()}
    assert {"atp", "captured_at"}.issubset(fields)


def test_alert_timestamp_must_be_utc() -> None:
    with pytest.raises(ValidationError):
        WeatherAlert(
            alert_id="NWS-1",
            event="Flood Watch",
            state="TX",
            area_description="Dallas County",
            headline="Flood Watch",
            starts_at=datetime.fromisoformat("2026-09-23T12:00:00+04:00"),
            ends_at=NOW,
            source="NOAA",
        )
    with pytest.raises(ValidationError, match="ends_at must be later"):
        WeatherAlert(
            alert_id="NWS-2", event="Flood Watch", state="TX",
            area_description="Dallas County", starts_at=NOW, ends_at=NOW, source="NOAA",
        )


def test_incident_requires_provenance_and_round_trips_json() -> None:
    payload = {
        "incident_id": "WX-1",
        "incident_type": "Weather",
        "source_record_id": "NWS-1",
        "event": "Flood Watch",
        "status": "ok",
        "created_at": NOW,
    }
    with pytest.raises(ValidationError) as error:
        Incident(**payload)
    assert error.value.errors()[0]["loc"] == ("provenance",)

    incident = Incident(
        **payload,
        provenance=ProvenanceRecord(
            external_signal=DataMode.LIVE,
            internal_operations=DataMode.SYNTHETIC,
            explanation=DataMode.CALCULATED,
            dataset_version="fixtures-v1",
            as_of=NOW,
        ),
    )
    restored = Incident.model_validate_json(incident.model_dump_json())
    assert restored == incident


def test_sourcing_lane_rank_and_service_level_are_validated() -> None:
    with pytest.raises(ValidationError):
        StoreSkuSourcingLane(
            store_id="108",
            sku="WATER",
            dc_id="DC-1",
            rank=0,
            transit_hours=-1,
            service_level="primary",
        )


def test_product_moq_must_align_to_case_pack() -> None:
    with pytest.raises(ValidationError, match="whole multiple"):
        Product(sku="WATER", name="Water", category="Emergency", case_pack=6, moq=10)


def test_inventory_freshness_requires_utc_evaluation_time() -> None:
    snapshot = InventorySnapshot(
        location_id="DC-1", sku="WATER", on_hand=10, committed=0,
        safety_stock=0, eligible_inbound_before_cutoff=0, atp=10, captured_at=NOW,
    )
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        snapshot.is_fresh(at=datetime(2026, 9, 23, 13), max_age_hours=24)
