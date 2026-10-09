"""Pin the Weather inbox's client-facing presentation rules.

These cover the three things a reviewer reads first and that were previously wrong:
geography and demand evidence must stay separate, the recommended action must be
specific enough to execute, and an exported table must say what produced it.
"""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import pytest
from market_intelligence.util.tables import drop_empty_columns
from market_intelligence.weather.inbox_helpers import replenishment_action_text
from market_intelligence.weather.inbox_helpers import replenishment_available_qty
from market_intelligence.weather.inbox_helpers import replenishment_planned_qty
from market_intelligence.weather.inbox_helpers import replenishment_serving_dc
from market_intelligence.weather.inbox_helpers import weather_actionability
from market_intelligence.weather.inbox_helpers import weather_alert_timing
from market_intelligence.weather.inbox_helpers import weather_evidence_strength
from market_intelligence.weather.inbox_helpers import weather_inbox_export_csv
from market_intelligence.weather.inbox_helpers import weather_recommended_action
from market_intelligence.weather.inbox_helpers import weather_store_exposure_label


PROJECT_ROOT = Path(__file__).resolve().parents[2]




def _row(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "Store Match": "NOAA polygon",
        "Demand Basis": "Exact Scenario Match",
        "Operational Priority": "High",
        "Store IDs": "101, 104",
        "Store Names": "101 (Dallas, TX); 104 (Houston, TX)",
        "Store Count": 2,
        "Units To Move": 240,
        "Residual Units": 0,
        "Primary DC": "DC-TX1",
        "Primary Planned Units": 240,
        "Backup DC": "",
        "Backup Planned Units": 0,
        "Route Risk": "No",
        "Staffing Risk": "Low",
        "Action Deadline": "within 18h, before the alert begins",
    }
    base.update(overrides)
    return base


def test_geography_answer_never_stands_in_for_the_demand_answer() -> None:
    """A county-name text match must not be reported as exact scenario evidence."""
    strength = weather_evidence_strength

    # Polygon + exact cases is the only combination strong enough to claim Strong.
    assert strength(_row()) == "Strong"
    assert strength(_row(**{"Store Match": "County fallback"})) == "Moderate"
    # No Store matched, so there is nothing to size, whatever the geography column says.
    assert strength(_row(**{
        "Store Match": "No Store intersection",
        "Demand Basis": "Not assessed - no Store match",
    })) == "Limited"


def test_no_store_match_is_never_actionable() -> None:
    actionability = weather_actionability
    assert actionability(_row()) == "Act"
    assert actionability(_row(**{
        "Store Match": "No Store intersection",
        "Demand Basis": "Not assessed - no Store match",
        "Operational Priority": "Medium",
        "Store IDs": "",
        "Store Count": 0,
    })) == "Monitor"


def test_recommended_action_names_units_store_dc_and_deadline() -> None:
    """"Review inventory" is identical on every row and cannot be executed."""
    action = weather_recommended_action(_row())
    assert "240 units" in action
    assert "101 (Dallas, TX)" in action
    assert "DC-TX1" in action
    assert "within 18h" in action


def test_recommended_action_escalates_the_uncovered_remainder() -> None:
    action = weather_recommended_action(
        _row(**{"Primary Planned Units": 180, "Residual Units": 60})
    )
    assert "60 units" in action
    assert "escalate" in action.lower()


def test_recommended_action_calls_out_an_exposed_route() -> None:
    action = weather_recommended_action(_row(**{"Route Risk": "Yes"}))
    assert "confirm a safe delivery lane" in action.lower()


def test_recommended_action_matches_the_dc_replenishment_plans_actual_sourcing() -> None:
    """The inbox must never credit a DC the replenishment plan shows supplying nothing.

    Regression for the exact case Ajith would catch: the inbox said "release 549 units
    from Dallas Regional DC" while the DC Replenishment Plan showed Dallas planning 0
    units and a Texas Alternate DC covering 498 -- two sections describing one decision
    two different ways.
    """
    action = weather_recommended_action(_row(**{
        "Units To Move": 549,
        "Primary DC": "Dallas Regional DC",
        "Primary Planned Units": 0,
        "Backup DC": "Texas Alternate DC",
        "Backup Planned Units": 498,
        "Residual Units": 51,
    }))
    assert "Dallas Regional DC" not in action
    assert "498 units" in action
    assert "Texas Alternate DC" in action
    assert "51 units" in action
    assert "escalate" in action.lower()


def test_recommended_action_says_no_dc_can_supply_rather_than_naming_one_with_zero_units() -> None:
    """Zero DCs able to help must read as "no DC can supply", never "release 0 units"."""
    action = weather_recommended_action(_row(**{
        "Units To Move": 200,
        "Primary Planned Units": 0,
        "Backup DC": "",
        "Backup Planned Units": 0,
        "Residual Units": 200,
    }))
    assert "DC-TX1" not in action
    assert "no dc can supply" in action.lower()
    assert "200 units" in action


def test_alert_timing_measures_an_active_alert_against_its_end() -> None:
    timing = weather_alert_timing
    now = pd.Timestamp.now(tz="UTC")

    future_phrase, future_deadline = timing(
        {"onset": (now + pd.Timedelta(hours=6)).isoformat(), "ends": (now + pd.Timedelta(hours=30)).isoformat()}
    )
    assert "starts in" in future_phrase
    assert "before the alert begins" in future_deadline

    active_phrase, active_deadline = timing(
        {"onset": (now - pd.Timedelta(hours=2)).isoformat(), "ends": (now + pd.Timedelta(hours=9)).isoformat()}
    )
    assert "active now" in active_phrase
    assert "before the alert ends" in active_deadline

    closed_phrase, closed_deadline = timing(
        {"onset": (now - pd.Timedelta(hours=9)).isoformat(), "ends": (now - pd.Timedelta(hours=1)).isoformat()}
    )
    assert closed_phrase == "window closed"
    assert "already closed" in closed_deadline

    assert timing({}) == ("", "before the alert window closes")


def test_store_exposure_carries_locations_not_bare_ids() -> None:
    label = weather_store_exposure_label("2 Stores · 101, 104")
    assert label.startswith("2 Stores · ")
    assert "101 (" in label and "104 (" in label
    # A scope that is not a list of IDs is passed through untouched.
    assert label != "2 Stores · 101, 104"
    assert weather_store_exposure_label("No Store match · 8 evaluated") == (
        "No Store match · 8 evaluated"
    )
    assert weather_store_exposure_label("Not quantified") == "Not quantified"


def test_a_column_blank_on_every_row_is_not_shown() -> None:
    """A column of blanks reads as a defect; a column with one blank is information."""
    drop = drop_empty_columns
    df = pd.DataFrame(
        [
            {"Incident Reference": "WX-1", "Source": "", "Store Match": "NOAA polygon", "Notes": "x"},
            {"Incident Reference": "WX-2", "Source": "", "Store Match": "County fallback", "Notes": ""},
        ]
    )
    kept = drop(df, ["Incident Reference", "Source", "Store Match", "Notes", "Absent"])
    assert kept == ["Incident Reference", "Store Match", "Notes"]


def _replenishment_row(**overrides: Any) -> Dict[str, Any]:
    """A single Store/Product row from the DC Replenishment Plan's scenario_df, using the
    raw field names the simplified table's helpers read (distinct from weather_recommended_action's
    incident-level "Units To Move" / "Primary Planned Units" naming above)."""
    base: Dict[str, Any] = {
        "Primary DC": "Dallas Regional DC",
        "Primary DC Available ATP": 188,
        "Primary Planned Qty": 48,
        "Backup DC": "No eligible alternate",
        "Backup DC Available ATP": 0,
        "Backup Planned Qty": 0,
        "Order Quantity": 48,
        "Residual Gap": 0,
        "Replenishment Plan": "advance-ship 48 units from Dallas Regional DC before the alert window",
        "Route Risk": "No",
    }
    base.update(overrides)
    return base


def test_serving_dc_names_only_the_dc_that_actually_ships() -> None:
    serving_dc = replenishment_serving_dc
    assert serving_dc(_replenishment_row()) == "Dallas Regional DC"


def test_serving_dc_reflects_the_100_unit_single_sourcing_example_from_the_23_sep_transcript() -> None:
    """Regression for the exact example discussed with Ajith on 23 Sep: "if 100 units are
    required, the nearest DC has only 50, and another eligible DC has 100, the business may
    prefer the single DC that can fulfill all 100 rather than automatically splitting 50 + 50."

    The Primary DC (50 ATP) cannot cover the 100-unit requirement alone, so
    allocate_product_requirement() sources the whole 100 from the one alternate DC that can,
    rather than splitting 50 from Primary + 50 from Backup. Serving DC must name only that
    one DC -- never the Primary, which shipped nothing.
    """
    serving_dc = replenishment_serving_dc
    available_qty = replenishment_available_qty
    planned_qty = replenishment_planned_qty
    row = _replenishment_row(**{
        "Primary DC Available ATP": 50,
        "Primary Planned Qty": 0,
        "Backup DC": "Texas Alternate DC",
        "Backup DC Available ATP": 100,
        "Backup Planned Qty": 100,
        "Order Quantity": 100,
        "Residual Gap": 0,
        "Replenishment Plan": (
            "route 100 units from Texas Alternate DC (approx. 3.0h demo transit) "
            "Allocation rule: Nearest eligible alternate supplies the complete requirement "
            "to avoid a split shipment."
        ),
    })
    assert serving_dc(row) == "Texas Alternate DC"
    assert "Dallas Regional DC" not in serving_dc(row)
    assert available_qty(row) == 100
    assert planned_qty(row) == 100
    action = replenishment_action_text(row)
    assert "avoid a split shipment" in action
    assert "Dallas Regional DC" not in action


def test_primary_dc_supplies_when_it_has_atp_and_lane_is_within_deadline() -> None:
    """Route exposure is a warning, not a reason to show Dallas ATP + 4-10h transit + 0 supply.

    This prevents the demo contradiction Ajith/Sumit flagged: when the Primary DC has
    enough ATP and the approved lane can arrive before the alert deadline, Planned Qty
    should reflect the movement. Route Risk is still shown in the action text for planner
    validation.
    """
    serving_dc = replenishment_serving_dc
    available_qty = replenishment_available_qty
    planned_qty = replenishment_planned_qty
    action_text = replenishment_action_text
    row = _replenishment_row(**{
        "Primary DC Available ATP": 188,
        "Primary Planned Qty": 48,
        "Backup DC": "No eligible alternate",
        "Backup DC Available ATP": 0,
        "Backup Planned Qty": 0,
        "Order Quantity": 48,
        "Residual Gap": 0,
        "Route Risk": "Yes",
        "Replenishment Plan": (
            "advance-ship 48 units from Dallas Regional DC before the alert window "
            "Allocation rule: Primary DC supplies the complete requirement."
        ),
    })
    assert serving_dc(row) == "Dallas Regional DC"
    assert available_qty(row) == 188
    assert planned_qty(row) == 48
    assert "primary delivery route is weather-exposed" in action_text(row)


def test_serving_dc_shows_both_dcs_when_a_split_is_a_last_resort() -> None:
    serving_dc = replenishment_serving_dc
    available_qty = replenishment_available_qty
    planned_qty = replenishment_planned_qty
    row = _replenishment_row(**{
        "Primary DC Available ATP": 30,
        "Primary Planned Qty": 30,
        "Backup DC": "Texas Alternate DC",
        "Backup DC Available ATP": 18,
        "Backup Planned Qty": 18,
        "Order Quantity": 48,
        "Residual Gap": 0,
    })
    assert serving_dc(row) == "Dallas Regional DC + Texas Alternate DC"
    assert available_qty(row) == 48
    assert planned_qty(row) == 48


def test_action_text_folds_route_risk_into_the_sentence() -> None:
    action_text = replenishment_action_text
    row = _replenishment_row(**{"Route Risk": "Yes"})
    action = action_text(row)
    assert "confirm a safe delivery lane" in action.lower()
    assert action.startswith("advance-ship 48 units")


def test_export_states_what_produced_it() -> None:
    """A CSV gets forwarded; on its own it must still distinguish live from synthetic."""
    inbox = pd.DataFrame(
        [
            {
                "Incident Reference": "WX-1",
                "Weather Event": "Flood Watch",
                "Store Match": "County fallback",
                "Demand Basis": "Analog Scenario Match",
                "Incident Summary": "Flood Watch in TX",
                "Store Match Reason": "3 analog case(s) found.",
            }
        ]
    )
    csv_bytes = weather_inbox_export_csv(
        inbox, ["Incident Reference", "Weather Event", "Store Match", "Demand Basis"]
    )
    text = csv_bytes.decode("utf-8")
    assert "Live NOAA alerts" in text
    assert "Synthetic internal dataset" in text
    assert "fixtures-v1" in text
    assert "Run Time (UTC)" in text
    assert "Demand Basis Reason" in text
    assert "Incident Summary" in text
