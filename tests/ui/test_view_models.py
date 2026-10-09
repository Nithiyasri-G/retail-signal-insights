from market_intelligence.provenance.models import DataProvenance
from market_intelligence.ui.configure import RunConfiguration
from market_intelligence.ui.demand_planner import quantity_column_help
from market_intelligence.ui.evidence import evidence_layer_cards
from market_intelligence.ui.operational_impact import (
    history_selector_label,
    normalize_missing_evidence,
    operational_status_line,
    operational_number_formats,
    replenishment_columns,
    weather_inbox_columns,
)
from market_intelligence.ui.results import result_summary_rows


def test_framework_independent_ui_view_models() -> None:
    assert RunConfiguration("", "US", ()).validate() == (
        "Retailer is required.", "At least one source must be enabled."
    )
    assert RunConfiguration("Retailer", "US", ("weather",)).validate() == ()
    assert "Order Quantity" in quantity_column_help()
    assert "Update Summary" not in weather_inbox_columns()
    assert "Area of Impact" in weather_inbox_columns()
    assert "Exposure Change" not in weather_inbox_columns()
    # Geography and demand evidence are separate questions and must stay in separate
    # columns; the old shared "Store Match Type" let a county-name match be reported as
    # a direct historical one.
    assert "Store Match" in weather_inbox_columns()
    assert "Demand Basis" in weather_inbox_columns()
    assert "Store Match Type" not in weather_inbox_columns()
    assert "Exposure Method" not in weather_inbox_columns()
    # Concatenation of four adjacent columns; it belongs in the export, not the table.
    assert "Incident Summary" not in weather_inbox_columns()
    assert weather_inbox_columns()[-2:] == ("Demand Basis", "Evidence Strength")
    assert "Primary DC" in replenishment_columns()
    assert operational_status_line("Not run", "Not run", 37) == (
        "Configured sources: Live NOAA/openFDA | Current run: Not run | Historical incidents available: 37"
    )
    assert normalize_missing_evidence("Precise NOAA geography → Store match") == (
        "NOAA geography → Store match evidence"
    )
    assert operational_number_formats(["Forecast Shortfall", "Order Quantity", "Event"]) == {
        "Forecast Shortfall": "{:,.0f}",
        "Order Quantity": "{:,.0f}",
    }
    assert history_selector_label(
        {"Incident ID": "WX-1", "Event": "Flood Watch", "State": "TX", "Created At": "2026-09-23T10:00:00Z"}
    ) == "WX-1 | Flood Watch | TX | 2026-09-23T10:00:00Z"
    assert result_summary_rows([{"source": "NOAA", "signal_name": "Flood", "risk_score": 7, "reason": "Severe"}])[0]["Risk"] == 7
    provenance = DataProvenance.live_signal_with_synthetic_operations(as_of="2026-09-23T12:00:00+00:00")
    assert evidence_layer_cards(provenance)[1] == "Internal operational data: Synthetic"
