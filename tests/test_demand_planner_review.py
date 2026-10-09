import pandas as pd

from market_intelligence.data.demo import demo_market_signals, demo_store_inventory, demo_store_master
from market_intelligence.demand.planning import (
    calculate_demand_plan,
    demand_plan_product_rows,
    display_plan,
    forecast_input_label,
    planning_mode,
)
from market_intelligence.llm.demand_report import local_demand_planner_report, report_contradicts_formulas


def _store(store_id="101"):
    return demo_store_inventory(demo_store_master()).query("`Store ID` == @store_id").drop(columns=["UPC"])


def test_store_id_is_not_reported_as_a_store_count():
    plan = calculate_demand_plan({}, _store(), "Store 101 - Dallas, TX")
    assert plan["Selected Store ID"] == "101" and plan["Stores Evaluated"] == 1
    assert "Store Scope" not in plan
    report = local_demand_planner_report(plan)
    assert "Store 101 in Dallas, TX" in report and "101 stores" not in report


def test_gap_and_surplus_wording_is_consistent():
    report = local_demand_planner_report(
        {"Baseline Gap": 735, "Baseline Surplus": 40, "Available Supply": 3644, "Products In Scope": 21}
    )
    assert "sum of individual product shortages" in report
    assert "40-unit surplus relative to their individual baseline forecasts" in report
    assert "historical average" not in report.lower().split("inventory impact")[1]
    assert "Committed inventory and Safety Stock are zero" in report


def test_display_labels_are_renamed():
    shown = display_plan({"Baseline Gap": 1, "Demand Direction": "x", "Forecast Feature": "y"})
    assert {"Current Baseline Supply Gap", "Planning Review Direction", "Potential Forecast Input"} <= set(shown)
    assert forecast_input_label("food_at_home_cpi_pressure") == "Food-at-home inflation pressure"


def test_context_only_signals_do_not_run_the_store_calculation():
    for key in ("gasoline_wallet_pressure_score", "headline_cpi_value_pressure_score"):
        assert planning_mode({"Signal Area": "Category CPI", "Signal Key": key})[0] == "context"
    mode, reason = planning_mode({"Signal Area": "Retail News"})
    assert mode == "context" and "No product/category/store mapping" in reason
    assert planning_mode({"Signal Area": "Category CPI", "Signal Key": "food_at_home_cpi_pressure_score"})[0] == "full"


def test_reference_signals_carry_their_mapping():
    modes = {row["Signal Area"]: planning_mode(row.to_dict())[0] for _, row in demo_market_signals().iterrows()}
    assert set(modes.values()) == {"full"}


def test_product_export_is_numeric_and_product_level():
    signal = {"Signal ID": "SIG-003", "Signal Area": "Category CPI", "Signal Key": "household_furnishings_cpi_pressure_score"}
    rows = demand_plan_product_rows(signal, _store(), "101", "Store 101 - Dallas, TX", "2026-10-09")
    assert len(rows) and set(rows["Category"]) == {"Household"}
    assert pd.api.types.is_integer_dtype(rows["Available Supply"])
    assert (rows["Product-Level Baseline Shortage"] >= 0).all()


def test_report_contradiction_guard():
    plan = {"Stores Evaluated": 1}
    assert report_contradicts_formulas("The store scope includes 101 stores.", plan)
    assert report_contradicts_formulas("Surplus is available supply minus historical average.", plan)
    assert not report_contradicts_formulas("Store 101 shows a 735-unit gap.", plan)
