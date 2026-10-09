import sqlite3

from market_intelligence.scenarios.service import (
    ScenarioRecord,
    list_scenarios,
    save_scenario,
)
from market_intelligence.ui.scenario_lab import live_incidents_only, scenario_banner


def test_scenario_write_never_creates_or_writes_operational_incidents(tmp_path) -> None:
    database = tmp_path / "scenario.db"
    record = ScenarioRecord.create(
        scenario_type="Weather",
        created_by="test-user",
        assumptions={"event": "Flood Watch", "state": "TX"},
        result={"status": "ok"},
    )

    save_scenario(database, record)

    assert list_scenarios(database)[0].scenario_id == record.scenario_id
    with sqlite3.connect(database) as connection:
        operational_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='operational_incidents'"
        ).fetchone()
    assert operational_table is None


def test_live_inbox_filter_excludes_controlled_demo_rows() -> None:
    rows = [
        {"Incident ID": "WX-LIVE", "Demo": 0, "Source": "Live NOAA"},
        {"Incident ID": "WX-DEMO", "Demo": 1, "Source": "Controlled Demo"},
    ]

    assert live_incidents_only(rows) == [rows[0]]


def test_scenario_lab_banner_is_unambiguous() -> None:
    assert scenario_banner() == (
        "Synthetic scenario - not an operational incident and not live NOAA/openFDA data."
    )
