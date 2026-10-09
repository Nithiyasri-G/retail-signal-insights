"""Pin the rules that keep internal detail off a client-facing screen.

Each test here corresponds to something that reached a client demo: the prompt printed
as a report, a green SUCCESS badge over a failure, another retailer's name as a column
header, and raw floats and timestamps in tables.
"""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import pytest

from market_intelligence.ui.operational_impact import (
    operational_number_formats,
    replenishment_columns,
)
from market_intelligence.llm.validation import BRIEF_SECTIONS_FULL
from market_intelligence.llm.validation import BRIEF_SECTIONS_ULTRA_COMPACT
from market_intelligence.demand.planning import DEMAND_SIGNAL_SCOPE_FIELDS
from market_intelligence.llm.validation import PLANNER_REPORT_SECTIONS
from market_intelligence.weather.assumptions import WEATHER_PHASE_ORDER
from market_intelligence.application.run_status import brief_pipeline_state
from market_intelligence.signals.signal_log import build_demand_signal_log
from market_intelligence.incidents.builders import build_recall_incidents
from market_intelligence.demand.planning import calculate_demand_plan
from market_intelligence.data.demo import demo_store_inventory
from market_intelligence.data.demo import demo_store_master
from market_intelligence.llm.audit import display_fallback_reason
from market_intelligence.util.tables import format_as_of_columns
from market_intelligence.util.tables import format_recall_date
from market_intelligence.llm.demand_report import local_demand_planner_report
from market_intelligence.demand.planning import signal_store_scope
from market_intelligence.util.tables import sum_numeric_column
from market_intelligence.llm.validation import validate_executive_brief
from market_intelligence.llm.validation import validate_generated_report
from market_intelligence.weather.assumptions import weather_affected_scope
from market_intelligence.signals.labels import with_client_labels


PROJECT_ROOT = Path(__file__).resolve().parents[2]

VALID_BRIEF = """EXECUTIVE SUMMARY
Inflation is the strongest signal this week. Weather risk is moderate in Texas.

TOP INSIGHTS
- Category CPI - BLS - High: food inflation is rising.
- Weather Risk - NOAA - Medium: active alerts in Texas.
- Retail News - GNews - Medium: competitor promotion pressure.

RECOMMENDED ACTIONS
- Validate category sell-through before adjusting the forecast; we need to confirm sell-through first.
- Confirm DC routes for the exposed stores where margin pressure is <5% of plan.
- Review the promotion calendar.

CONFIDENCE AND LIMITATIONS
External signals only; not validated against internal sales."""




# --- The brief validator -----------------------------------------------------------


def test_retry_is_judged_against_the_sections_it_was_asked_for() -> None:
    """The ultra-compact prompt omits PLANNING RELEVANCE on purpose.

    Checking every attempt against all five headings rejected correct retries for
    missing a section they were never asked to write, which forced the local brief.
    """
    validate = validate_executive_brief
    full = BRIEF_SECTIONS_FULL
    compact = BRIEF_SECTIONS_ULTRA_COMPACT

    assert validate(VALID_BRIEF, compact) == (True, "")
    assert validate(VALID_BRIEF, full) == (False, "missing headings: PLANNING RELEVANCE")


def test_ordinary_business_english_is_not_treated_as_a_template() -> None:
    """"we need to confirm sell-through" and "<5% of plan" are prose, not placeholders."""
    validate = validate_executive_brief
    compact = BRIEF_SECTIONS_ULTRA_COMPACT
    assert "we need to confirm" in VALID_BRIEF
    assert "<5%" in VALID_BRIEF
    assert validate(VALID_BRIEF, compact)[0] is True


def test_a_real_template_and_a_real_prompt_echo_are_still_rejected() -> None:
    validate = validate_executive_brief
    compact = BRIEF_SECTIONS_ULTRA_COMPACT

    template = VALID_BRIEF.replace("Inflation is the strongest signal this week.", "<2 concise sentences>")
    ok, reason = validate(template, compact)
    assert ok is False and "unfilled template slot" in reason

    echo = VALID_BRIEF.replace(
        "Inflation is the strongest signal this week.",
        "We need to produce the required sections exactly as specified, using only supplied payload.",
    )
    ok, reason = validate(echo, compact)
    assert ok is False and "meta language" in reason


def test_the_planning_report_is_validated_too() -> None:
    """This path had no guard, so the prompt itself reached the screen and the download."""
    validate = validate_generated_report
    sections = PLANNER_REPORT_SECTIONS

    observed_leak = (
        "PAYLOAD DETAILS\nEXTERNAL_SIGNAL / CALCULATED_PLANNING_RESULT\n"
        "We need to produce the required sections exactly as specified, using only supplied payload.\n"
        "PLANNING SUMMARY\n<2 concise sentences>\nFORECAST CONTEXT\n<explain baseline forecast>\n"
        "INVENTORY IMPACT"
    )
    assert validate(observed_leak, sections)[0] is False

    local = local_demand_planner_report(
        {
            "Signal Area": "Weather Risk",
            "Signal Level": "High",
            "Demand Direction": "SURGE",
            "Baseline Forecast": 4339,
            "Historical Average": 4100,
            "Available Supply": 3807,
            "Baseline Gap": 532,
            "Baseline Surplus": 0,
            "Reference Products": 21,
        }
    )
    assert validate(local, sections) == (True, "")


# --- Honest status reporting -------------------------------------------------------


def test_the_brief_step_does_not_badge_a_fallback_as_success() -> None:
    state = brief_pipeline_state
    assert state("nvidia", {})["status"] == "success"
    fallback = state("fallback", {"fallback_reason": "NVIDIA response failed semantic validation"})
    assert fallback["status"] != "success"
    assert "failed" not in fallback["detail"].lower()


def test_internal_diagnostics_are_translated_for_a_business_reader() -> None:
    shown = display_fallback_reason(
        "NVIDIA response failed semantic validation: template or meta language detected"
    )
    assert "semantic validation" not in shown
    assert "rule-based summary" in shown


# --- Nothing that names another retailer, and no Python identifiers on screen -------


def test_no_other_retailer_is_named_anywhere_in_the_application() -> None:
    # The application's source is app.py plus the whole market_intelligence package
    # (code and stylesheet), so scan all of it rather than only the entry script.
    paths = [PROJECT_ROOT / "app.py"]
    for pattern in ("*.py", "*.css"):
        paths.extend(sorted((PROJECT_ROOT / "market_intelligence").rglob(pattern)))
    for path in paths:
        source = path.read_text(encoding="utf-8").lower()
        for name in ("dollar general",):
            assert name not in source, f"{name!r} still appears in {path.name}"


def test_client_tables_get_title_case_headers() -> None:
    labelled = with_client_labels(
        pd.DataFrame([{"retail_category": "Snacks", "enterprise_kpi": "Safety", "score_reason": "3 recalls"}])
    )
    assert list(labelled.columns) == ["Retail Category", "Enterprise KPI", "Why This Level"]


# --- Table formatting --------------------------------------------------------------


def test_renamed_display_columns_still_get_a_number_format() -> None:
    """A display rename used to drop the format, which is why cells read 157.000000."""
    formats = operational_number_formats(
        ["Baseline", "Primary ATP", "Backup ATP", "Remaining Gap", "Order Quantity", "Product"]
    )
    assert formats["Baseline"] == "{:,.0f}"
    assert formats["Primary ATP"] == "{:,.0f}"
    assert formats["Order Quantity"] == "{:,.0f}"
    assert "Product" not in formats


def test_inventory_as_of_renders_as_a_date() -> None:
    formatted = format_as_of_columns(
        pd.DataFrame([{"Store Inventory As Of": "2026-09-23T00:00:00+00:00", "Product": "Water"}])
    )
    assert formatted["Store Inventory As Of"].tolist() == ["23 Sep 2026"]
    assert formatted["Product"].tolist() == ["Water"]


def test_the_alternate_dc_is_inside_the_visible_column_set() -> None:
    """The per-product alternate DC is the point of the table; it cannot be off-screen."""
    columns = replenishment_columns()
    assert "Backup DC" in columns
    assert "Backup DC Status" in columns  # OIC-1: eligibility status, not a name suffix
    assert "Backup Planned Qty" in columns
    assert "Store Name" in columns
    # OIC-1 added exactly one necessary column (Backup DC Status) on top of the prior
    # 14-column cap from the original "eighteen columns -> horizontal scrollbar" fix;
    # the full ineligibility reason text still stays out of this table, in the
    # Replenishment Plan column, so the table doesn't widen further than this.
    assert len(columns) <= 15


# --- Weather presentation ----------------------------------------------------------


def test_event_phases_read_in_chronological_order() -> None:
    order = WEATHER_PHASE_ORDER
    assert sorted(["During-Event", "Post-Event", "Pre-Event"], key=lambda p: order[p]) == [
        "Pre-Event",
        "During-Event",
        "Post-Event",
    ]


def test_affected_scope_has_no_internal_branch_name() -> None:
    scope = weather_affected_scope(1, "TX", "County name fallback")
    assert scope == "1 Store in TX (matched on county name)"
    assert weather_affected_scope(3, "TX", "County name fallback").startswith("3 Stores")
    assert "fallback" not in scope.lower()


def _planning_signal(area: str, **extra: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "Signal ID": "SIG-001",
        "Signal Area": area,
        "Level": "High",
        "Source": "test",
        "Signal Key": "",
        "Top Event": "",
        "Catalog Categories": [],
    }
    base.update(extra)
    return base


def test_the_demand_plan_is_scoped_to_what_the_signal_implicates() -> None:
    """Two different signals against one store must not produce the identical figure.

    They used to: the calculation totalled every product regardless of the signal, so the
    tab read as a smaller copy of the Operational Impact Center.
    """
    calculate = calculate_demand_plan
    store = demo_store_inventory()
    store = store[store["Store ID"] == "108"].reset_index(drop=True)

    flood = calculate(
        _planning_signal("Weather Risk", **{"Signal Key": "supply_chain_weather_risk_score", "Top Event": "Flood Watch"}),
        store,
    )
    heat = calculate(
        _planning_signal("Weather Risk", **{"Signal Key": "supply_chain_weather_risk_score", "Top Event": "Excessive Heat Warning"}),
        store,
    )
    household_cpi = calculate(
        _planning_signal("Category CPI", **{"Signal Key": "household_furnishings_cpi_pressure_score"}),
        store,
    )

    assert flood["Products In Scope"] < flood["Products In Store"]
    assert {flood["Baseline Gap"], heat["Baseline Gap"], household_cpi["Baseline Gap"]} != {flood["Baseline Gap"]}
    assert "Emergency Essentials" in flood["Category Scope"]
    assert "Personal Care" in heat["Category Scope"]
    assert household_cpi["Category Scope"] == "Household"
    assert flood["Planning Scope"].lower().startswith("categories")


def test_an_unmappable_signal_says_so_rather_than_guessing() -> None:
    calculate = calculate_demand_plan
    store = demo_store_inventory()
    store = store[store["Store ID"] == "108"].reset_index(drop=True)

    news = calculate(_planning_signal("Retail News", **{"Signal Key": "news_risk_score"}), store)
    assert news["Products In Scope"] == news["Products In Store"]
    assert "not category-mapped" in news["Planning Scope"]
    assert "internal hierarchy" in news["Scope Basis"]

    # Headline inflation moves the basket, not one shelf, and should not be faked.
    headline = calculate(_planning_signal("Inflation", **{"Signal Key": "inflation_pressure_score"}), store)
    assert headline["Products In Scope"] == headline["Products In Store"]
    assert "whole basket" in headline["Scope Basis"]


def test_scope_does_not_fall_back_to_the_whole_store_when_no_category_is_carried() -> None:
    """A mapped category the store does not carry is zero products in scope, not an unrelated whole-store result."""
    calculate = calculate_demand_plan
    store = pd.DataFrame(
        [
            {"Store ID": "999", "Product": "Notebook", "Category": "Stationery",
             "Baseline Forecast": 100, "Historical Average": 90, "On Hand": 40, "Inbound": 10},
        ]
    )
    plan = calculate(
        _planning_signal("Weather Risk", **{"Signal Key": "supply_chain_weather_risk_score", "Top Event": "Flood Watch"}),
        store,
    )
    assert plan["Products In Scope"] == 0 and plan["Baseline Gap"] == 0
    assert "No products in the mapped category are carried at the selected store" in plan["Scope Basis"]


def test_a_signal_only_reaches_the_stores_in_its_geography() -> None:
    """A Texas alert assessed against a Georgia store cannot reconcile with the OIC.

    The store picker listed all sixteen with no marking, so the two views could silently
    be discussing different stores.
    """
    scope = signal_store_scope
    stores = demo_store_master()

    texas, basis = scope({"Region": "TX", "Signal Area": "Weather Risk"}, stores)
    assert texas and all(str(s).startswith("1") for s in texas)
    assert "TX" in basis

    georgia, _ = scope({"Region": "GA", "Signal Area": "Weather Risk"}, stores)
    assert set(texas).isdisjoint(georgia)

    # A national signal reaches every store, so nothing is marked out of scope.
    national, national_basis = scope({"Region": "US", "Signal Area": "Category CPI"}, stores)
    assert national == ()
    assert "national" in national_basis

    # A state with no store degrades to baseline context rather than an empty picker.
    none_there, none_basis = scope({"Region": "NY", "Signal Area": "Weather Risk"}, stores)
    assert none_there == ()
    assert "No store sits in NY" in none_basis


def test_the_signal_log_hides_its_own_plumbing() -> None:
    """Signal Key / Top Event / Catalog Categories drive the scope; they are not content."""
    feature_df = pd.DataFrame(
        [
            {
                "signal_area": "Weather Risk", "region": "TX", "risk_score": 7.0, "source": "NOAA",
                "signal_name": "supply_chain_weather_risk_score", "top_event": "Flood Watch",
                "demand_direction": "up", "forecast_feature": "f",
                "business_impact": "b", "recommended_action": "r",
            }
        ]
    )
    log = build_demand_signal_log(feature_df)
    hidden = DEMAND_SIGNAL_SCOPE_FIELDS

    # Present in the data, because the calculation needs them...
    assert all(field in log.columns for field in hidden)
    # ...and absent from what the planner reads.
    shown = [c for c in log.columns if c not in hidden]
    assert shown == [
        "Signal ID", "Signal Area", "Region", "Level", "Source",
        "Demand Direction", "Forecast Feature", "Business Impact", "Recommended Action",
    ]


def _recall_item(**overrides: Any) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "product": "Creamy Peanut Butter 16oz jars",
        "recall_number": "F-1200-2026",
        "event_id": "EV1",
        "reason": "Undeclared peanut allergen",
        "state": "TX",
        "classification": "Class I",
        "status": "Ongoing",
        "recall_date": "20260918",
        "distribution_pattern": "TX, GA, and 4 other states",
        "recalling_firm": "Acme Foods Inc",
        "upcs": "",
        "lots": "",
        "sku_match_status": "unknown",
        "risk_type": "allergen_risk",
        "risk_score": 7.0,
    }
    item.update(overrides)
    return item


def test_a_class_i_recall_is_not_capped_at_medium() -> None:
    """Match confidence and hazard severity are different things.

    Every text-matched recall was pinned to Medium, so a Class I recall -- the FDA class
    for a reasonable probability of serious harm -- sat at Medium across sixteen stores.
    """
    build = build_recall_incidents

    class_one = build({"status": "success", "items": [_recall_item()]}, "run")[0]
    class_two = build(
        {"status": "success", "items": [_recall_item(classification="Class II", recall_number="F-1201-2026")]},
        "run",
    )[0]

    assert class_one["Priority"] == "High"
    assert class_two["Priority"] == "Medium"
    # The match is still qualified rather than presented as confirmed.
    assert any("Text-only match" in limitation for limitation in class_one["Limitations"])
    assert any("not because the product match is confirmed" in limitation for limitation in class_one["Limitations"])


def test_recalls_of_one_product_are_told_apart() -> None:
    """Five Peanut Butter recalls differed only by Incident ID -- the weather complaint again."""
    build = build_recall_incidents
    firms = ["Acme Foods Inc", "Bright Valley Foods", "Cedar Mills LLC", "Delta Snacks Co", "Evergreen Pantry"]
    items = [
        _recall_item(recalling_firm=firm, recall_number=f"F-{1200 + i}-2026", event_id=f"EV{i}", reason=f"Reason {i}")
        for i, firm in enumerate(firms)
    ]
    incidents = build({"status": "success", "items": items}, "run")
    assert len(incidents) == 5

    # The fields the inbox shows must differ, not just the generated ID.
    signatures = {
        (
            inc["Evidence"]["recall_number"],
            inc["Evidence"]["recalling_firm"],
            inc["Evidence"]["reason"],
        )
        for inc in incidents
    }
    assert len(signatures) == 5


def test_openfda_recall_dates_render_as_dates() -> None:
    fmt = format_recall_date
    assert fmt("20260918") == "18 Sep 2026"
    assert fmt("") == "Not supplied"
    assert fmt(None) == "Not supplied"
    assert fmt("not a date") == "not a date"


def test_single_sourcing_follows_the_rule_stated_in_the_review() -> None:
    """Need 100; nearest DC has 50; another has 100 -- take 100 from the one DC.

    Ajit's example in the 24 Sep review. A 50/50 split across two DCs is what the
    replenishment plan must not do when one DC can cover the whole requirement.
    """
    from market_intelligence.replenishment.allocation import allocate_product_requirement

    covered = allocate_product_requirement(
        store_id="108",
        sku="0731683",
        required_quantity=100,
        primary={"dc_id": "DC-TX1", "dc_name": "Dallas Regional DC", "atp": 50, "eta_hours": 3, "eligible": True},
        alternates=[
            {"dc_id": "DC-TX2", "dc_name": "Texas Alternate DC", "atp": 100, "eta_hours": 6, "eligible": True},
            {"dc_id": "DC-GA1", "dc_name": "Atlanta Regional DC", "atp": 160, "eta_hours": 14, "eligible": True},
        ],
    )
    assert covered.primary_supply == 0
    assert covered.backup_supply == 100
    assert covered.remaining_gap == 0
    # Nearest DC that can cover the whole requirement, not simply the largest.
    assert covered.backup_dc_name == "Texas Alternate DC"

    # Sep 29 review reinforcement: even when no single DC can cover the whole
    # requirement, do NOT split partial quantities across primary + backup. The full
    # requirement stays as Remaining Gap for escalation. (Replaces the stale Sep-24
    # expectation of a 50/40/10 split, which the code no longer implements -- see
    # allocation.py:105-109's own comment documenting this as a deliberate policy.)
    no_single_dc_can_cover = allocate_product_requirement(
        store_id="108",
        sku="0731683",
        required_quantity=100,
        primary={"dc_id": "DC-TX1", "dc_name": "Dallas Regional DC", "atp": 50, "eta_hours": 3, "eligible": True},
        alternates=[{"dc_id": "DC-TX2", "dc_name": "Texas Alternate DC", "atp": 40, "eta_hours": 6, "eligible": True}],
    )
    assert (
        no_single_dc_can_cover.primary_supply,
        no_single_dc_can_cover.backup_supply,
        no_single_dc_can_cover.remaining_gap,
    ) == (0, 0, 100)
    assert "no split shipment" in no_single_dc_can_cover.allocation_reason
    assert "escalate" in no_single_dc_can_cover.allocation_reason

    # A backup with ATP but ineligible (e.g. misses the ship-by deadline) is filtered
    # out of candidate selection entirely at the allocation layer -- it is not credited
    # with any supply, and the full gap remains. (app.py's display layer separately
    # surfaces the nearest ineligible candidate by name/ATP for planner transparency;
    # that is a presentation concern, not part of this allocation-layer contract.)
    ineligible_backup = allocate_product_requirement(
        store_id="108",
        sku="0731683",
        required_quantity=100,
        primary={"dc_id": "DC-TX1", "dc_name": "Dallas Regional DC", "atp": 50, "eta_hours": 3, "eligible": True},
        alternates=[
            {"dc_id": "DC-TX2", "dc_name": "Texas Alternate DC", "atp": 200, "eta_hours": 6, "eligible": False},
        ],
    )
    assert ineligible_backup.backup_supply == 0
    assert ineligible_backup.backup_dc_name == "No eligible alternate"
    assert ineligible_backup.remaining_gap == 100
    assert ineligible_backup.allocation_reason.startswith("Primary DC has partial ATP only")


def test_a_missing_column_totals_to_zero_instead_of_crashing() -> None:
    """Persisted incidents from earlier runs do not all carry every column.

    `DataFrame.get(column, 0)` returns the scalar 0 when the column is absent, and a
    scalar has no .fillna(), so opening the Weather tab against older history raised
    "'int' object has no attribute 'fillna'".
    """
    total = sum_numeric_column
    old_shape = pd.DataFrame([{"Store ID": "101", "Product": "Bottled Water", "Inventory Gap": 40.0}])

    assert total(old_shape, "Order Quantity") == 0.0
    assert total(old_shape, "Residual Gap") == 0.0
    assert total(old_shape, "Inventory Gap") == 40.0
    assert total(pd.DataFrame(), "Order Quantity") == 0.0
    assert total(None, "Order Quantity") == 0.0
    assert total([], "Order Quantity") == 0.0
    # Non-numeric entries are coerced away rather than raising.
    assert total(pd.DataFrame([{"Order Quantity": "n/a"}, {"Order Quantity": 12}]), "Order Quantity") == 12.0


def test_unknown_severity_is_reported_as_a_noaa_classification() -> None:
    from market_intelligence.weather.inbox import build_weather_inbox_row

    row = build_weather_inbox_row(
        {"Evidence": {"alert": {"severity": "Unknown", "urgency": "Unknown", "certainty": "Unknown"}}}
    )
    assert row.noaa_profile == "Not classified by NOAA"
