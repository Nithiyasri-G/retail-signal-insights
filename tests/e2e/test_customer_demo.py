from __future__ import annotations

from decimal import Decimal

from market_intelligence.provenance.models import DataProvenance
from market_intelligence.provenance.presentation import provenance_summary
from market_intelligence.replenishment.allocation import allocate_product_requirement
from market_intelligence.replenishment.quantity import calculate_order_quantity
from market_intelligence.scenarios.service import ScenarioRecord, list_scenarios, save_scenario
from market_intelligence.ui.scenario_lab import live_incidents_only
from market_intelligence.weather.geography import match_alert_to_stores
from market_intelligence.weather.inbox import build_weather_inbox_row


def _weather_incident(incident_id: str, area: str, headline: str, starts: str) -> dict:
    return {
        "Incident ID": incident_id,
        "Type": "Weather",
        "Event": "Flood Watch",
        "Priority": "Medium",
        "Decision Status": "Proposed",
        "Affected Scope": area,
        "Evidence": {
            "source": "NOAA Weather Alerts",
            "alert": {
                "event": "Flood Watch",
                "area_desc": area,
                "headline": headline,
                "onset": starts,
                "ends": "2026-09-24T12:00:00+00:00",
            },
        },
        "Scenario": {
            "match_precision": "County name fallback",
            "evidence_code": "MATCH_COUNTY_NAME_FALLBACK",
        },
    }


def _dc(dc_id: str, name: str, atp: float, eta: float) -> dict:
    return {"dc_id": dc_id, "dc_name": name, "atp": atp, "eta_hours": eta, "eligible": True}


def test_customer_demo_decision_chain(tmp_path) -> None:
    alerts = [
        _weather_incident("WX-1", "Dallas County, TX", "Flood Watch for Dallas", "2026-09-23T18:00:00+00:00"),
        _weather_incident("WX-2", "Tarrant County, TX", "Flood Watch for Tarrant", "2026-09-23T20:00:00+00:00"),
        _weather_incident("WX-3", "Travis County, TX", "Flood Watch for Travis", "2026-09-24T01:00:00+00:00"),
    ]
    inbox = [build_weather_inbox_row(alert) for alert in alerts]
    assert len({(row.alert_area, row.headline, row.starts_at) for row in inbox}) == 3

    stores = [
        {"Store ID": "108", "State": "TX", "County": "Dallas County"},
        {"Store ID": "204", "State": "TX", "County": "Harris County"},
    ]
    mapped = match_alert_to_stores("Dallas County, TX", "TX", stores)
    no_intersection = match_alert_to_stores("Travis County, TX", "TX", stores)
    assert mapped.affected_store_ids == ("108",)
    assert mapped.method == "County name fallback"
    assert no_intersection.evidence_code == "NO_STORE_INTERSECTION"

    product_a = allocate_product_requirement(
        store_id="108",
        sku="WATER",
        required_quantity=24,
        primary=_dc("TX1", "Dallas DC", 0, 2),
        alternates=[_dc("TX2", "Fort Worth DC", 24, 4), _dc("TX3", "Austin DC", 100, 7)],
    )
    product_b = allocate_product_requirement(
        store_id="108",
        sku="BATTERY",
        required_quantity=72,
        primary=_dc("TX1", "Dallas DC", 0, 2),
        alternates=[_dc("TX2", "Fort Worth DC", 20, 4), _dc("TX3", "Austin DC", 72, 7)],
    )
    assert (product_a.backup_dc_id, product_b.backup_dc_id) == ("TX2", "TX3")

    rounded = calculate_order_quantity(Decimal("22.6"), case_pack=6, moq=0)
    assert rounded.order_quantity == 24
    assert rounded.forecast_shortfall == Decimal("22.6")

    database = tmp_path / "demo.db"
    scenario = ScenarioRecord.create(
        scenario_type="Weather",
        created_by="customer-demo",
        assumptions={"event": "Flood Watch", "state": "TX"},
        result={"status": "ok"},
    )
    save_scenario(database, scenario)
    assert len(list_scenarios(database)) == 1
    assert live_incidents_only([{"Incident ID": "WX-1", "Demo": 0}, {"Incident ID": scenario.scenario_id, "Demo": 1}]) == [
        {"Incident ID": "WX-1", "Demo": 0}
    ]

    provenance = DataProvenance.live_signal_with_synthetic_operations(
        as_of="2026-09-23T12:00:00+00:00"
    )
    assert "No Mock Data Used" not in provenance_summary(provenance)
    assert "External signal: Live" in provenance_summary(provenance)
    assert "Internal operational data: Synthetic" in provenance_summary(provenance)
