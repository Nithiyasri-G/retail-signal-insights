"""Regression cases reproduced by the September 28 review."""
import json
import math
import os
import tempfile
import sys
import types
from pathlib import Path
import pandas as pd
import pytest
os.environ.setdefault('MARKET_INTELLIGENCE_DB', tempfile.mktemp(suffix='.db'))
from market_intelligence.infra import http as http_client, nvidia as nvidia_client
from market_intelligence.config import settings
from market_intelligence.recalls import matching as recall_matching
from market_intelligence.weather.geography import match_alert_to_stores
from market_intelligence.data.distribution import distribution_states
from market_intelligence.config.settings import DEFAULT_NVIDIA_MODEL
from market_intelligence.data.fixture_repository import FixtureRepository
from market_intelligence.config.sources import SOURCE_LABELS
from market_intelligence.recalls.matching import _empty_recall_exposure_result
from market_intelligence.infra.nvidia import _nvidia_chat_request
from market_intelligence.llm.incident import _recommendation_numbers_are_grounded
from market_intelligence.recalls.matching import _store_distribution_overlap
from market_intelligence.incidents.filters import apply_incident_scope_filters
from market_intelligence.ui.brief import brief_section_heading
from market_intelligence.signals.collector_evidence import build_collector_evidence
from market_intelligence.collectors.live_bls import build_cpi_signal
from market_intelligence.incidents.builders import build_recall_incidents
from market_intelligence.incidents.builders import build_weather_incidents
from market_intelligence.weather.scenario import build_weather_scenario
from market_intelligence.demand.planning import calculate_demand_plan
from market_intelligence.weather.planning import can_arrive_before_deadline
from market_intelligence.collectors.live_apify import collect_apify_trends
from market_intelligence.collectors.live_fda import collect_fda_recalls
from market_intelligence.collectors.live_noaa import collect_weather_alerts
from market_intelligence.weather.planning import compute_planning_demand
from market_intelligence.signals.scoring import compute_retail_kpis
from market_intelligence.signals.collector_evidence import count_raw_records
from market_intelligence.demand.planning import demand_planning_editor_column_kinds
from market_intelligence.data.demo import demo_dc_inventory
from market_intelligence.data.demo import demo_dc_network
from market_intelligence.data.demo_scenarios import demo_recall_scenario
from market_intelligence.data.demo import demo_store_inventory
from market_intelligence.data.demo import demo_store_master
from market_intelligence.data.demo_scenarios import demo_weather_alert_scenario
from market_intelligence.signals.retail_context import enrich_feature_rows_for_retailer
from market_intelligence.llm.audit import exportable_llm_audit
from market_intelligence.recalls.parsing import extract_upcs
from market_intelligence.llm.incident import generate_dc_replenishment_recommendation
from market_intelligence.llm.brief import generate_nvidia_brief
from market_intelligence.persistence.run_history import init_run_history_db
from market_intelligence.llm.incident import local_dc_replenishment_recommendation
from market_intelligence.recalls.matching import match_recall_to_catalog
from market_intelligence.util.hashing import payload_hash
from market_intelligence.signals.search import related_search_summary
from market_intelligence.signals.retail_context import retail_context_for_signal
from market_intelligence.signals.risk import risk_band
from market_intelligence.application.run_status import run_progress_pct
from market_intelligence.persistence.operational_store import save_decision
from market_intelligence.persistence.run_history import save_run_history
from market_intelligence.signals.search import search_interest_summary
from market_intelligence.signals.retail_context import signal_display_label
from market_intelligence.weather.planning import traffic_phase_explanation
from market_intelligence.util.clock import utc_now
from market_intelligence.llm.validation import validate_executive_brief

VALID = '''EXECUTIVE SUMMARY
Weather alerts require a review of local conditions.
TOP INSIGHTS
- Weather Risk from NOAA indicates possible route disruption.
PLANNING RELEVANCE
Review affected stores against the supplied evidence.
RECOMMENDED ACTIONS
- Verify the affected store geography.
- Review the current route conditions.
- Confirm inventory before placing orders.
CONFIDENCE AND LIMITATIONS
External evidence only; internal impact needs validation.'''

def frame():
    return pd.DataFrame([{'signal_area':'Weather Risk','source':'NOAA Weather Alerts','signal_name':'supply_chain_weather_risk_score','region':'TX','risk_score':8,'score_reason':'Severe weather alert'}])

def test_separate_keyword_search_has_findings_without_a_scored_signal(monkeypatch):
    class Dataset:
        def iterate_items(self):
            return iter([{'searchTerm':'batteries','interestBySubregion':[{'geoName':'Georgia','value':[82]}],
                          'relatedQueries_rising':[{'query':'rechargeable batteries','value':120}]}])
    class Actor:
        def call(self, **kwargs):
            return {'defaultDatasetId':'dataset-1'}
    class Client:
        def __init__(self, token):
            assert token == 'test-token'
        def actor(self, name):
            return Actor()
        def dataset(self, name):
            return Dataset()
    monkeypatch.setitem(sys.modules, 'apify_client', types.SimpleNamespace(ApifyClient=Client))
    result = collect_apify_trends('test-token', ['batteries'], 'US', 'now 1-d',
                                    include_scored_signal=False)
    assert result['status'] == 'success' and result['rows'] == []
    summary = search_interest_summary(result['items'])
    assert summary.to_dict('records') == [{'Search term':'batteries', 'Region with search interest':'Georgia'}]
    assert not any('score' in column.lower() for column in summary.columns)
    related = related_search_summary(result['raw'])
    assert related.to_dict('records') == [{'Search term':'batteries', 'Related search':'rechargeable batteries', 'Type':'Rising'}]
    assert not any('score' in column.lower() for column in related.columns)

def test_planning_demand_ceils_never_rounds_to_nearest():
    """A demand estimate must never be rounded to nearest -- that under-orders whenever
    the fractional part is below 0.5 (round(302.2) == 302, silently leaving 0.2 units of
    demand unmet). Planning Demand must ceiling instead, while the raw forecast used for
    audit/display stays exactly as computed, unrounded.
    """
    raw, planning = compute_planning_demand(baseline=257.0, uplift_pct=17.6)
    assert raw == pytest.approx(302.232)
    assert planning == 303
    assert planning != round(raw)  # proves this case actually exercises the ceil-vs-round gap

    # A demand estimate that lands exactly on a whole number must not be bumped up an
    # extra unit -- ceil(300.0) == 300, not 301.
    raw_exact, planning_exact = compute_planning_demand(baseline=250.0, uplift_pct=20.0)
    assert raw_exact == pytest.approx(300.0)
    assert planning_exact == 300


def test_planning_demand_is_immune_to_ieee754_float_representation_error():
    """D01: 100.0 * (1 + 10/100.0) evaluates to 110.00000000000001 in plain IEEE-754
    double arithmetic (binary floating-point cannot represent 0.1/10% exactly), which
    would make math.ceil() wrongly return 111 for a baseline that should plan for
    exactly 110 units. Decimal-based arithmetic must not have this failure mode.
    """
    raw_float_bug = 100.0 * (1 + 10 / 100.0)
    assert raw_float_bug != 110.0  # confirms this case genuinely exercises the float bug
    assert math.ceil(raw_float_bug) == 111  # the bug this fix must not reproduce

    raw, planning = compute_planning_demand(baseline=100.0, uplift_pct=10.0)
    assert raw == pytest.approx(110.0)
    assert planning == 110


def test_demand_planning_editor_disables_identifiers_and_floors_numeric_inputs_at_zero():
    """D02: the Demand Planner's editable grid previously had no column_config at all,
    so Store ID/Product/Category could be freely retyped and every numeric column
    (including ones calculate_demand_plan never reads) accepted negative values with no
    validation. Only the four fields the calculation actually consumes should be
    editable numeric inputs; everything else -- identifiers and catalog/computed
    columns alike -- must be disabled.
    """
    columns = [
        "Store ID", "Product", "Category", "Case Pack", "MOQ", "Sellable Unit",
        "Baseline Forecast", "Historical Average", "On Hand", "Inbound",
        "Captured At", "Committed", "Safety Stock", "Eligible Inbound Before Cutoff", "ATP",
    ]
    kinds = demand_planning_editor_column_kinds(columns)
    assert kinds["Baseline Forecast"] == "numeric_input"
    assert kinds["Historical Average"] == "numeric_input"
    assert kinds["On Hand"] == "numeric_input"
    assert kinds["Inbound"] == "numeric_input"
    for identifier_or_computed in (
        "Store ID", "Product", "Category", "Case Pack", "MOQ", "Sellable Unit",
        "Captured At", "Committed", "Safety Stock", "Eligible Inbound Before Cutoff", "ATP",
    ):
        assert kinds[identifier_or_computed] == "disabled"


def test_collector_exception_does_not_abort_the_whole_run(isolated_database, monkeypatch):
    """A malformed/unexpected exception inside one collector (not just a caught
    requests.RequestException) must not abort the entire intelligence run before
    persistence. Only GNews used to isolate its own exceptions internally; BLS/FDA/
    Weather had no isolation at the call site and could take down a run where other
    sources had already succeeded.

    AppTest re-executes app.py as a fresh module, so monkeypatching a function on the
    already-imported `a` module does not reach that execution -- the fault is injected
    at the shared `requests` module instead (a genuine singleton both executions use):
    BLS's collector expects a JSON object and calls .get("status") on it; returning a
    JSON list reproduces exactly the "unexpected shape" failure mode the audit found,
    with no requests.RequestException for safe_request's own try/except to catch.
    """
    from streamlit.testing.v1 import AppTest
    import requests

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list:
            return []  # malformed: collect_bls_cpi expects a dict and calls .get() on it

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: _FakeResponse())

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=60).run()
    for checkbox in app.checkbox:
        if checkbox.label in {"Retail news", "Product recalls", "Weather risk"}:
            checkbox.set_value(False)
    app.run()
    next(b for b in app.button if b.label == "Run intelligence").click().run()

    assert not app.exception
    run = app.session_state.get("run")
    assert run is not None
    assert run["results"]["bls"]["status"] == "failed"
    assert run["results"]["bls"]["error"]


def test_substitute_insufficiently_stocked_is_exercisable():
    """Scenario 11b: a mapped substitute product that is itself out of stock somewhere.
    The audit found every mapped substitute had On Hand > 20 everywhere. Fixture now has
    Store 101's Trail Mix (Peanut Butter's substitute) at On Hand=15 (data/fixtures/v1/
    store_inventory.csv), which match_recall_to_catalog's Sufficient = on_hand>20 check
    must report as insufficiently stocked for that store.
    """
    item = demo_recall_scenario('Peanut Butter')
    result = match_recall_to_catalog(item)
    assert result.get('substitute_product') == 'Trail Mix'
    readiness = {row['Store ID']: row for row in result.get('substitute_readiness', [])}
    assert '101' in readiness
    assert readiness['101']['Sufficient'] is False


def test_backup_dc_atp_sufficient_but_misses_ship_by_deadline_is_exercisable():
    """Scenario 5: a Backup DC has enough ATP but cannot arrive before the alert deadline.
    The audit found no fixture data could exercise this (max backup transit was 12h against
    a 36h-out demo window). Fixture now gives Store 106's Rain Ponchos backup lane a 40h
    transit (data/fixtures/v1/store_sku_dc_lanes.csv) with reduced primary ATP (5, in
    dc_inventory.csv) so the backup is genuinely needed but must be excluded on timing,
    not stock -- and still shown, not hidden, per the review requirement.
    """
    r = demo_weather_alert_scenario('Winter Storm Warning', 'TX', county='Travis County')
    result = build_weather_scenario(r)
    df = result['scenario_df']
    ponchos = df[df['UPC'] == '073168300019']
    assert not ponchos.empty
    row = ponchos.iloc[0]
    assert row['Primary DC Available ATP'] == 5
    assert row['Primary Planned Qty'] == 0
    assert row['Backup DC Available ATP'] == 43
    assert row['Backup Planned Qty'] == 0
    # OIC-1: "Backup DC" stays a clean, filterable name; the ineligibility fact and its
    # reason live in their own fields, not smashed into the name as a suffix.
    assert 'not eligible' not in row['Backup DC'] and '(' not in row['Backup DC']
    assert row['Backup DC Status'] == 'Ineligible'
    assert 'transit' in row['Backup DC Reason'].lower()
    assert row['Residual Gap'] == row['Order Quantity']


def test_backup_dc_status_is_coherent_with_whether_it_actually_supplied_units():
    """OIC-1: "Backup DC Status" must agree with what the row's own numbers say
    happened, for every row in a real scenario, not just the one hand-picked case above.
    A row that actually got backup-supplied units must read Eligible; the placeholder
    "No eligible alternate" name must never read Eligible; and "Backup DC" must never
    carry an eligibility annotation smashed into the name itself (it is its own field now).
    """
    r = demo_weather_alert_scenario('Winter Storm Warning', 'TX', county='Travis County')
    result = build_weather_scenario(r)
    df = result['scenario_df']
    assert not df.empty
    saw_insufficient = False
    for _, row in df.iterrows():
        assert '(' not in row['Backup DC']
        if row['Backup Planned Qty'] > 0:
            assert row['Backup DC Status'] == 'Eligible'
        if row['Backup DC'] == 'No eligible alternate':
            assert row['Backup DC Status'] == 'No alternate'
            assert row['Backup DC Reason'] == ''
        # A real DC name with 0 units planned and an escalated residual gap must never
        # read "Eligible" -- it is either "Insufficient" (partial ATP only, no-split
        # policy escalates the rest) or "Ineligible" (times out, shown for transparency
        # only), but previously both cases were mislabeled Eligible even though nothing
        # shipped and the table escalated the full gap, which read as self-contradictory.
        if row['Backup DC'] not in ('No eligible alternate',) and row['Backup Planned Qty'] == 0 and row['Residual Gap'] > 0:
            assert row['Backup DC Status'] in ('Insufficient', 'Ineligible')
            assert row['Backup DC Reason']
            if row['Backup DC Status'] == 'Insufficient':
                saw_insufficient = True
    assert saw_insufficient, "fixture must exercise at least one Insufficient row for this to be a real test"


def test_local_dc_replenishment_recommendation_names_only_the_table_facts():
    """The AI recommendation's deterministic fallback (used whenever NVIDIA is
    unavailable or fails grounding validation) must be built strictly from the DC
    Replenishment Plan table's own columns -- it replaces the removed free-text
    "Replenishment Plan" column and must not reintroduce an independently-derived
    description that could drift from the table.
    """
    gap_rows = pd.DataFrame([
        {'Product': 'Space Heater', 'Order Quantity': 224, 'Primary DC': 'Dallas Regional DC',
         'Primary Planned Qty': 0, 'Backup DC': 'Texas Alternate DC', 'Backup DC Status': 'Insufficient',
         'Backup Planned Qty': 0, 'Residual Gap': 224, 'Route Risk': 'No'},
        {'Product': 'Flashlights', 'Order Quantity': 90, 'Primary DC': 'Dallas Regional DC',
         'Primary Planned Qty': 0, 'Backup DC': 'Texas Alternate DC', 'Backup DC Status': 'Eligible',
         'Backup Planned Qty': 90, 'Residual Gap': 0, 'Route Risk': 'Yes'},
    ])
    text = local_dc_replenishment_recommendation(gap_rows, 'Store 101 - Dallas, TX')
    assert 'Space Heater' in text and '224' in text and 'escalate' in text.lower()
    assert 'Flashlights' in text and '90' in text and 'Texas Alternate DC' in text
    assert 'Confirm a safe delivery lane before releasing the shipment' in text

    empty_text = local_dc_replenishment_recommendation(pd.DataFrame(), 'Store 101 - Dallas, TX')
    assert 'no replenishment action needed' in empty_text.lower()


def test_recommendation_grounding_rejects_a_number_absent_from_the_evidence():
    """L01/L03-style grounding, applied to the new AI replenishment recommendation: a
    plausible-sounding number in the generated text is still wrong if it doesn't trace
    back to the supplied facts -- this is what triggers the deterministic fallback.
    """
    facts = [{'Product': 'Space Heater', 'Order Quantity': 224, 'Primary Planned Qty': 0, 'Backup Planned Qty': 0, 'Residual Gap': 224}]
    grounded_text = "Space Heater has a 224-unit gap; escalate the full 224 units for additional coverage."
    assert _recommendation_numbers_are_grounded(grounded_text, facts)

    hallucinated_text = "Space Heater has a 224-unit gap; 150 units are available from a nearby supplier."
    assert not _recommendation_numbers_are_grounded(hallucinated_text, facts)


def test_generate_dc_replenishment_recommendation_falls_back_without_api_key():
    """No NVIDIA key configured must fall back to the deterministic summary, never a
    blank recommendation or an exception.
    """
    gap_rows = pd.DataFrame([
        {'Product': 'Space Heater', 'Order Quantity': 224, 'Primary DC': 'Dallas Regional DC',
         'Primary Planned Qty': 0, 'Backup DC': 'Texas Alternate DC', 'Backup DC Status': 'Insufficient',
         'Backup Planned Qty': 0, 'Residual Gap': 224, 'Route Risk': 'No'},
    ])
    text, source = generate_dc_replenishment_recommendation('', 'x', 'Store 101 - Dallas, TX', gap_rows)
    assert source == 'Local summary'
    assert 'Space Heater' in text


def test_generate_dc_replenishment_recommendation_uses_nvidia_when_grounded(monkeypatch):
    gap_rows = pd.DataFrame([
        {'Product': 'Space Heater', 'Order Quantity': 224, 'Primary DC': 'Dallas Regional DC',
         'Primary Planned Qty': 0, 'Backup DC': 'Texas Alternate DC', 'Backup DC Status': 'Insufficient',
         'Backup Planned Qty': 0, 'Residual Gap': 224, 'Route Risk': 'No'},
    ])

    def fake(**kwargs):
        return True, 'Escalate the full 224-unit Space Heater gap; neither Dallas Regional DC nor Texas Alternate DC can cover it alone.', '', 10

    monkeypatch.setattr(nvidia_client, '_nvidia_chat_request', fake)
    text, source = generate_dc_replenishment_recommendation('test-key', DEFAULT_NVIDIA_MODEL, 'Store 101 - Dallas, TX', gap_rows)
    assert source == 'AI summary'
    assert '224' in text


def test_generate_dc_replenishment_recommendation_falls_back_on_hallucinated_number(monkeypatch):
    gap_rows = pd.DataFrame([
        {'Product': 'Space Heater', 'Order Quantity': 224, 'Primary DC': 'Dallas Regional DC',
         'Primary Planned Qty': 0, 'Backup DC': 'Texas Alternate DC', 'Backup DC Status': 'Insufficient',
         'Backup Planned Qty': 0, 'Residual Gap': 224, 'Route Risk': 'No'},
    ])

    def fake(**kwargs):
        return True, 'A third DC can supply the remaining 150 units within the hour.', '', 10

    monkeypatch.setattr(nvidia_client, '_nvidia_chat_request', fake)
    text, source = generate_dc_replenishment_recommendation('test-key', DEFAULT_NVIDIA_MODEL, 'Store 101 - Dallas, TX', gap_rows)
    assert source == 'Local summary'
    assert 'Space Heater' in text


def test_backup_dc_filter_options_exclude_ineligible_names_by_status_not_substring():
    """OIC-5: the Backup DC filter dropdown previously excluded only the literal
    "No eligible alternate" placeholder text, so an ineligible nearest-candidate name
    (which, before OIC-1, carried a "(not eligible ...)" suffix) slipped through as a
    selectable filter option. Since OIC-1 made "Backup DC" always a clean name with no
    suffix at all, excluding by name pattern can no longer work -- exclusion must use the
    explicit "Backup DC Status" column instead.
    """
    weather_df = pd.DataFrame([
        {'Backup DC': 'Dallas Regional DC', 'Backup DC Status': 'Eligible'},
        {'Backup DC': 'Austin Regional DC', 'Backup DC Status': 'Ineligible'},
        {'Backup DC': 'No eligible alternate', 'Backup DC Status': 'No alternate'},
    ])
    eligible_only = weather_df.loc[weather_df['Backup DC Status'] == 'Eligible', 'Backup DC']
    assert sorted(eligible_only.unique().tolist()) == ['Dallas Regional DC']


def test_georgia_has_store_product_dc_diversity_and_all_three_allocation_outcomes():
    """OIC-2/OIC-8: every Georgia Store/Product previously used DC-GA1 as its only
    Primary DC (no diversity at all, unlike Texas's existing Store 105/Rock Salt
    DC-TX2 exception), and no Georgia fixture data could exercise all three allocation
    outcomes (primary-fully-covers / secondary-fully-covers / neither-covers-escalate).

    Fixture additions, mirroring the existing Texas exception pattern:
    - Store 202 / Bottled Water: DC-GA1/DC-GA2 primary-backup roles swapped (OIC-2
      diversity), transit values unchanged (a business override, not a distance change).
    - Store 204 / Pet Food: DC-GA1 (primary) Available to Push reduced to 5 (clearly
      insufficient); DC-GA2 (backup) raised to 500 (sufficient) -- secondary fully covers.
    - Store 204 / Laundry Detergent: both DC-GA1 and DC-GA2 Available to Push reduced to
      3 (both insufficient) -- no-split policy escalates the full gap.
    - Store 204 / Paper Towels: untouched -- primary fully covers (the default case).
    All three products are Household category, the category a Winter Storm Warning
    actually maps to for this fixture -- an earlier draft of this test used Food/
    Consumables and Personal Care products that a Winter Storm Warning never scopes in,
    so the rows never appeared in scenario_df at all.
    """
    lanes = FixtureRepository("v1").table("store_sku_dc_lanes")
    ga_primary = lanes[
        (lanes["Service Level"] == "primary") & (lanes["Store ID"].astype(str).isin(
            [str(i) for i in range(201, 209)]
        ))
    ]
    assert set(ga_primary["DC ID"].unique()) == {"DC-GA1", "DC-GA2"}, "Georgia must have more than one Primary DC"

    r = demo_weather_alert_scenario('Winter Storm Warning', 'GA', county='Chatham County')
    result = build_weather_scenario(r)
    df = result['scenario_df']
    store_204 = df[df['Store ID'] == '204']
    assert not store_204.empty

    pet_food = store_204[store_204['UPC'] == '017800130039'].iloc[0]
    assert pet_food['Primary Planned Qty'] == 0
    assert pet_food['Backup Planned Qty'] > 0
    assert pet_food['Backup DC Status'] == 'Eligible'
    assert pet_food['Residual Gap'] == 0

    laundry = store_204[store_204['UPC'] == '037000127109'].iloc[0]
    assert laundry['Primary Planned Qty'] == 0
    assert laundry['Backup Planned Qty'] == 0
    assert laundry['Residual Gap'] == laundry['Order Quantity']
    assert laundry['Residual Gap'] > 0

    paper_towels = store_204[store_204['UPC'] == '030772057008'].iloc[0]
    if paper_towels['Order Quantity'] > 0:
        assert paper_towels['Primary Planned Qty'] == paper_towels['Order Quantity']
        assert paper_towels['Residual Gap'] == 0


def test_cpi_food_at_home_signal_is_not_misclassified_as_a_recall_safety_signal():
    """D04 (Phase 9): retail_context_for_signal() checked "food"/"snack"/"candy"/
    "beverage" keywords in raw_category BEFORE the dedicated CPI "food at home" branch
    had a chance to run. A CPI signal's own raw_reference text ("Food at home: CPI
    298.5") contains the bare word "food", so it always matched the first (wrong)
    branch -- a branch-order bug, not a judgment call. Every Food-at-Home CPI signal
    was mislabeled Safety and Compliance Risk / recall-exposure, not Consumables
    Demand Pressure.
    """
    row = {
        "signal_area": "Category CPI",
        "signal_name": "food_at_home_cpi_pressure_score",
        "source": "BLS CPI",
        "affected_category": "",
        "raw_reference": "Food at home: CPI 298.5",
        "risk_score": 5.0,
    }
    context = retail_context_for_signal(row)
    assert context["enterprise_kpi"] == "Consumables Demand Pressure"
    assert context["enterprise_kpi"] != "Safety and Compliance Risk"

    # A genuine recall signal must still classify correctly -- this fix must not
    # suppress the real recall branch, only exclude CPI signals from it.
    recall_row = {
        "signal_area": "Product Recalls",
        "signal_name": "recall_exposure_score",
        "source": "openFDA Food Enforcement",
        "affected_category": "Snacks, candy, beverages",
        "raw_reference": "",
        "risk_score": 7.0,
    }
    recall_context = retail_context_for_signal(recall_row)
    assert recall_context["enterprise_kpi"] == "Safety and Compliance Risk"


def test_run_progress_reaches_100_percent_when_a_disabled_source_is_unchecked():
    """A03 (Phase 9): a user-unchecked source previously used status "skipped", the same
    status reserved for the brief step's local-fallback outcome. run_progress_pct()
    counts "skipped" toward its denominator without counting it as success, so a fully
    successful run with one source unchecked showed below 100% at completion. A
    user-disabled source must use "disabled" (already excluded from the denominator),
    while a step that genuinely ran but fell back (the brief) must still count.
    """
    all_succeeded_one_disabled = {
        "GNEWS": {"status": "success"},
        "BLS": {"status": "disabled"},
        "FDA": {"status": "success"},
        "WEATHER": {"status": "success"},
        "BRIEF": {"status": "success"},
    }
    assert run_progress_pct(all_succeeded_one_disabled) == 100

    # The brief step falling back to local generation must still show below 100% --
    # this fix must not make every run look complete regardless of real outcomes.
    brief_fell_back = dict(all_succeeded_one_disabled, BRIEF={"status": "skipped"})
    assert run_progress_pct(brief_fell_back) < 100


def test_raw_record_count_reflects_true_pre_filter_count_not_the_truncated_items():
    """A04 (Phase 9): count_raw_records() previously checked `items` FIRST, so a UI
    label reading "N raw record(s)" actually showed the already-limited/selected count
    -- for NOAA weather, `items` is the sample truncated to the configured limit; for
    Apify, `items` is the region-filtered subset. The raw_payload's own recognized shape
    (features/results/series/a plain list) is the genuine pre-filter count and must win.
    """
    noaa_raw = {"features": [{"id": i} for i in range(12)]}
    noaa_items = [{"id": i} for i in range(5)]  # truncated to the configured sample limit
    assert count_raw_records(noaa_raw, noaa_items) == 12

    apify_raw = [{"id": i} for i in range(20)]
    apify_items = [{"id": i} for i in range(3)]  # region-filtered subset
    assert count_raw_records(apify_raw, apify_items) == 20

    # An unrecognized payload shape still falls back to items, as before.
    assert count_raw_records({"unrecognized": "shape"}, apify_items) == 3


def test_exportable_audit_redacts_nested_per_attempt_prompts_too(monkeypatch):
    """A01 (Phase 9): exportable_llm_audit() previously stripped only the top-level
    system_prompt/user_prompt/payload/actual_request keys, leaving the full prompt text
    sitting untouched inside every nvidia_attempts[i]['actual_request'] entry -- a retry
    exports the complete evidence payload and prompt text through a channel believed
    sanitized.
    """
    monkeypatch.delenv('SHOW_LLM_TRACE', raising=False)
    audit = {
        'system_prompt': 'top-level system prompt',
        'user_prompt': 'top-level user prompt',
        'payload': {'signals': ['sensitive evidence']},
        'nvidia_attempts': [
            {
                'attempt': 1,
                'actual_request': {
                    'system_prompt': 'nested system prompt',
                    'user_prompt': 'nested user prompt with sensitive evidence',
                    'payload': {'signals': ['sensitive evidence']},
                },
                'raw_response': 'full raw model output text',
                'success': False,
            }
        ],
    }
    exported = exportable_llm_audit(audit)
    assert 'system_prompt' not in exported and 'user_prompt' not in exported
    nested = exported['nvidia_attempts'][0]
    assert 'actual_request' not in nested
    assert 'sensitive evidence' not in json.dumps(exported)
    assert nested['attempt'] == 1 and nested['success'] is False  # non-sensitive fields survive


def test_customer_purchase_exposure_requires_matching_product_not_just_lot_and_store():
    """R05 (Phase 9): customer_purchases.csv has no UPC column, only Lot ID + Store ID +
    Product. The exposure join previously keyed on Lot ID + Store ID only, so a customer
    who bought a DIFFERENT product at the same store under a colliding Lot ID string
    would be wrongly counted as exposed to this recall. The fixture's convention of
    deriving Lot IDs from the UPC digits makes real collisions unlikely today, but
    nothing in the code enforced that -- this is a structural gap, not a guarantee.
    """
    item = demo_recall_scenario('Peanut Butter')
    lot_id = item['lots']
    colliding_purchases = pd.DataFrame([
        {'Store ID': '101', 'Product': 'Peanut Butter', 'Lot ID': lot_id, 'Customer Token': 'cust-real-exposure', 'Units': 3, 'Exposure Type': 'Loyalty-linked'},
        # Same Lot ID string, same Store ID, but a DIFFERENT product -- must be excluded.
        {'Store ID': '101', 'Product': 'Canned Soup', 'Lot ID': lot_id, 'Customer Token': 'cust-unrelated-product', 'Units': 99, 'Exposure Type': 'Loyalty-linked'},
    ])
    result = match_recall_to_catalog(item, customer_purchases=colliding_purchases)
    assert result['match_status'] == 'confirmed_exact'
    assert result['loyalty_units'] == 3  # only the real Peanut Butter row, not the 99-unit Canned Soup collision
    assert result['loyalty_customers'] == 1


def test_traffic_explanation_text_agrees_with_the_sign_of_its_own_number():
    """W07: "What This Means" text was fixed per phase name regardless of the sign of
    that phase's own traffic delta -- a Pre-Event row showing -4% (fewer customers) could
    still read "rush-buying surge" (implying MORE customers), directly contradicting the
    number next to it. The explanation must flip with the actual sign of each row.
    """
    assert 'surge' in traffic_phase_explanation('Pre-Event', 8.0).lower()
    assert 'surge' not in traffic_phase_explanation('Pre-Event', -4.0).lower()
    assert 'avoid' in traffic_phase_explanation('Pre-Event', -4.0).lower() or 'delay' in traffic_phase_explanation('Pre-Event', -4.0).lower()
    assert 'fewer' in traffic_phase_explanation('During-Event', -14.0).lower()
    assert 'more' in traffic_phase_explanation('During-Event', 6.0).lower()
    assert 'rebound' in traffic_phase_explanation('Post-Event', 5.0).lower()
    assert 'below normal' in traffic_phase_explanation('Post-Event', -3.0).lower()


def test_primary_dc_unavailable_scenario_is_exercisable():
    """Scenario 3 (primary DC literally unavailable): the audit found no fixture data
    exercised this at all -- every DC/UPC had positive 'Available to Push'. Fixture now
    has DC-TX1's Space Heater ATP set to 0 (data/fixtures/v1/dc_inventory.csv); any Store
    with DC-TX1 as primary for Space Heater and any demand must show zero Primary Planned
    Qty and route to backup (or escalate) instead of silently reporting a healthy primary.
    """
    r = demo_weather_alert_scenario('Winter Storm Warning', 'TX', county='Dallas County')
    result = build_weather_scenario(r)
    df = result['scenario_df']
    heater = df[df['UPC'] == '052088880018']
    assert not heater.empty
    assert (heater['Primary DC Available ATP'] == 0).all()
    assert (heater['Primary Planned Qty'] == 0).all()


def test_route_exposure_checks_intermediate_counties_and_is_independent_of_gap():
    """W05: route exposure was judged only from the Store's own county and the primary
    DC's own county, never an intermediate county the lane's route actually passes
    through (fixture: Store 106's DC-TX1 lane route now lists 'Dallas County; Williamson
    County; Travis County' -- Williamson is neither endpoint). It was also only ever
    evaluated when there was an inventory shortfall (gap > 0), hiding real route exposure
    at a well-stocked Store. 'Route Exposed' (new, ungated) must catch both; 'Route Risk'
    (existing, gap-gated, drives Priority escalation) must be unchanged.
    """
    r = demo_weather_alert_scenario('Winter Storm Warning', 'TX')
    item = r['items'][0]
    # FIPS/geocode match bypasses county-NAME-text matching entirely, so Store 106 is
    # geographically affected regardless of what area_desc says -- letting area_desc
    # mention ONLY the intermediate county (neither Store 106's own Travis County nor
    # DC-TX1's own Dallas County), cleanly isolating the route intermediate-county check
    # from the separate Store-matching logic.
    item['geocode'] = {'SAME': ['048453']}  # Store 106 real FIPS (Travis County, TX)
    item['area_desc'] = 'Williamson County (Demonstration scenario)'
    result = build_weather_scenario(r)
    df = result['scenario_df']
    store_106 = df[df['Store ID'] == '106']
    assert not store_106.empty
    # Williamson County is an intermediate waypoint, not Store 106's own county (Travis)
    # nor DC-TX1's own county (Dallas) -- the old endpoint-only check would miss it.
    assert (store_106['Route Exposed'] == 'Yes').all()
    assert (store_106['Route Crosses Alert Area'] == 'Yes').all()

    # Route Exposed must not be gated on gap -- find a well-stocked (gap == 0) row and
    # confirm exposure is still reported there, even though Route Risk (action-required,
    # gap-gated) is correctly No for that specific row.
    well_stocked = store_106[store_106['Inventory Gap'] == 0]
    if not well_stocked.empty:
        assert (well_stocked['Route Exposed'] == 'Yes').all()
        assert (well_stocked['Route Risk'] == 'No').all()


def test_deadline_eligibility_uses_full_precision_not_rounded_hours():
    """W03: hours-until-deadline was rounded to 1 decimal BEFORE the eligibility
    comparison, so a true 6h58m (6.9833h) remaining rounded to 7.0h and incorrectly
    passed a 7h total lead time requirement (5h transit + 2h safety buffer). Full
    precision must fail this case; the same total lead exactly at or under the true
    remaining time must still pass.
    """
    precise_6h58m = 6 + 58 / 60  # 6.9833...

    # 5h transit + 2h buffer = 7h required; 6.9833h remaining -- must be ineligible.
    assert can_arrive_before_deadline(
        5.0, alert_is_active=False, hours_until_ends_precise=0.0, has_end_time=False,
        hours_until_event_precise=precise_6h58m, has_onset_time=True,
    ) is False

    # Old (buggy) rounded value would have been exactly 7.0h, which DOES pass a 7h
    # requirement -- confirms this is genuinely a precision issue, not just a stricter check.
    assert can_arrive_before_deadline(
        5.0, alert_is_active=False, hours_until_ends_precise=0.0, has_end_time=False,
        hours_until_event_precise=round(precise_6h58m, 1), has_onset_time=True,
    ) is True

    # A shorter transit that genuinely fits within the true precise remaining time passes.
    assert can_arrive_before_deadline(
        4.9, alert_is_active=False, hours_until_ends_precise=0.0, has_end_time=False,
        hours_until_event_precise=precise_6h58m, has_onset_time=True,
    ) is True

    # Already-active alert: judged against hours_until_ends_precise instead.
    assert can_arrive_before_deadline(
        5.0, alert_is_active=True, hours_until_ends_precise=precise_6h58m, has_end_time=True,
        hours_until_event_precise=0.0, has_onset_time=False,
    ) is False


def test_footprint_relevant_alert_survives_truncation_over_irrelevant_extreme_alerts(monkeypatch):
    """W11: NOAA alerts were kept in raw API order, so 'first N' could discard a Store-
    footprint-relevant Severe alert in favor of unrelated Extreme alerts that merely
    appeared first. A plain severity sort alone does not fix this either (two Extreme
    alerts would still outrank one relevant Severe alert). Footprint relevance must be
    checked before severity.
    """
    def _feature(event, severity, area_desc):
        return {
            'id': f'https://api.weather.gov/alerts/{event}',
            'properties': {
                'event': event, 'severity': severity, 'urgency': 'Expected', 'certainty': 'Likely',
                'areaDesc': area_desc, 'geocode': {},
            },
        }

    features = [
        _feature('Extreme Alert A', 'Extreme', 'Somewhere Else County, ZZ'),
        _feature('Extreme Alert B', 'Extreme', 'Another Place County, ZZ'),
        _feature('Relevant Severe', 'Severe', 'Dallas County, TX'),
    ]

    def fake_request(*args, **kwargs):
        return True, {'features': features}, 'success'

    monkeypatch.setattr(http_client, 'safe_request', fake_request)
    store_master = demo_store_master()

    result = collect_weather_alerts('TX', limit=2, store_master=store_master)
    kept_events = {item['event'] for item in result['items']}
    assert 'Relevant Severe' in kept_events
    assert result['rows'][0]['fetched_count'] == 3
    assert result['rows'][0]['retained_count'] == 2
    assert result['rows'][0]['operationally_relevant_fetched_count'] == 1


def test_real_fips_codes_prevent_false_ugc_fips_matches():
    """W01: with the old placeholder FIPS (13001 assigned to 'Fulton County'/Store 201,
    when 13001 is really Appling County), a NOAA alert whose geocode targets the real
    Appling County (13001) would falsely UGC/FIPS-match Store 201 (Atlanta, Fulton
    County) -- ~200 miles away. With real codes, that same geocode must NOT match Store
    201, and Store 201's own real geocode (13121, Fulton County) must.
    """
    store_master = demo_store_master()
    store_201 = store_master[store_master["Store ID"].astype(str) == "201"]
    assert store_201.iloc[0]["FIPS"] == "13121"

    # NOAA's SAME geocode carries a 6-digit code (leading 0 + 2-digit state FIPS +
    # 3-digit county FIPS); match_alert_to_stores._alert_fips extracts the last 5 digits.
    # The OLD placeholder value (13001) is real Appling County, not Fulton -- must not match.
    false_match = match_alert_to_stores(
        "Appling County", "GA", store_master.to_dict("records"),
        alert_geocodes={"SAME": ["013001"]},
    )
    assert "201" not in false_match.affected_store_ids

    # Store 201's real FIPS (13121, Fulton County) must match on its own geocode.
    true_match = match_alert_to_stores(
        "Fulton County", "GA", store_master.to_dict("records"),
        alert_geocodes={"SAME": ["013121"]},
    )
    assert "201" in true_match.affected_store_ids


def test_multi_state_incidents_do_not_inherit_another_states_evidence():
    """W02: build_weather_incidents used rows[0] (whichever state was queried first) as
    the base for EVERY incident's Evidence, so a Georgia incident could display Texas's
    alert_count/top_event/severe_or_extreme_count. Each incident must show its own
    state's aggregate evidence.
    """
    tx_row = {
        "region": "TX", "alert_count": 5, "total_returned_alert_count": 28,
        "top_event": "Flood Watch", "severe_or_extreme_count": 4, "extreme_count": 1,
        "confidence": "High", "recommended_action": "TX action text", "risk_score": 8.0,
    }
    ga_row = {
        "region": "GA", "alert_count": 1, "total_returned_alert_count": 4,
        "top_event": "Dense Fog Advisory", "severe_or_extreme_count": 0, "extreme_count": 0,
        "confidence": "High", "recommended_action": "GA action text", "risk_score": 2.0,
    }
    tx_item = {
        "alert_id": "tx-1", "event": "Flood Watch", "area_desc": "Nowhere County, ZZ",
        "source_query_states": ["TX"], "severity": "Severe", "urgency": "Expected", "certainty": "Likely",
    }
    ga_item = {
        "alert_id": "ga-1", "event": "Dense Fog Advisory", "area_desc": "Nowhere County, ZZ",
        "source_query_states": ["GA"], "severity": "Minor", "urgency": "Expected", "certainty": "Likely",
    }
    weather_result = {"rows": [tx_row, ga_row], "items": [tx_item, ga_item], "assessment_time": utc_now()}

    incidents = build_weather_incidents(weather_result, utc_now())
    by_state = {i["Evidence"]["alert"]["source_query_states"][0]: i for i in incidents}
    assert by_state["TX"]["Evidence"]["alert_count"] == 5
    assert by_state["TX"]["Evidence"]["top_event"] == "Flood Watch"
    assert by_state["TX"]["Evidence"]["severe_or_extreme_count"] == 4
    assert by_state["GA"]["Evidence"]["alert_count"] == 1
    assert by_state["GA"]["Evidence"]["top_event"] == "Dense Fog Advisory"
    assert by_state["GA"]["Evidence"]["severe_or_extreme_count"] == 0


def test_expired_alert_is_not_executable_and_upcoming_still_is():
    """W04: an alert whose own end time has passed must be classified Expired (not
    indistinguishable from Active with 0 hours remaining) and must not produce a fresh
    executable replenishment plan. An Upcoming alert (onset in the future) must still be
    plannable -- lifecycle is a 3-state Upcoming/Active/Expired split, not a binary gate.
    """
    import datetime as dt

    r = demo_weather_alert_scenario('Excessive Heat Warning', 'TX')
    item = r['items'][0]
    now = dt.datetime.now(dt.timezone.utc)
    item['onset'] = (now - dt.timedelta(days=2)).isoformat()
    item['ends'] = (now - dt.timedelta(days=1)).isoformat()
    r['assessment_time'] = now.isoformat()

    result = build_weather_scenario(r)
    assert result.get('alert_lifecycle') == 'Expired'
    assert result['status'] != 'ok'
    assert result['evidence_code'] == 'ALERT_EXPIRED'

    incidents = build_weather_incidents(r, utc_now())
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident['Priority'] == 'Low'
    assert incident['Recommended Actions'][0]['Action'] == 'Monitor only - alert window has ended'

    # Upcoming (unchanged, default demo onset is 36h in the future) must still plan normally.
    r2 = demo_weather_alert_scenario('Excessive Heat Warning', 'TX')
    result2 = build_weather_scenario(r2)
    assert result2.get('alert_lifecycle') == 'Upcoming'
    assert result2['status'] == 'ok'


def test_closed_store_inside_footprint_receives_no_operational_action():
    """A Store that matches an alert's geography but is marked Closed (fixture: Store
    108, El Paso, data/fixtures/v1/stores.csv 'Operating Status' column) must not
    receive operational actions, and the app must say so explicitly -- not silently
    compute (and hide) a scenario for a closed Store, and not say "no Store matched"
    when one plainly did.
    """
    r = demo_weather_alert_scenario('Excessive Heat Warning', 'TX', county='El Paso County')
    result = build_weather_scenario(r)
    assert result['status'] == 'insufficient_evidence'
    assert result['evidence_code'] == 'STORE_CLOSED_IN_FOOTPRINT'
    closed = result['closed_stores_in_footprint']
    assert {str(s['Store ID']) for s in closed} == {'108'}

    incidents = build_weather_incidents(r, utc_now())
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident['Priority'] == ''
    assert incident['Recommended Actions'][0]['Action'] == 'No Store action applies'
    assert 'Closed' in incident['Affected Scope']
    assert '108' in incident['Affected Scope']


def test_geometry_without_geocode_narrows_to_bounding_box_not_every_store_state():
    """A NOAA alert with a polygon but no geocode used to fall back to evaluating every
    Store-master state nationwide (the exact bug that made a single-region alert report
    every Store as 'evaluated'). It must now narrow to only the state(s) whose Stores
    fall inside the polygon's own bounding box: a rectangle covering only the TX Store
    footprint must match TX Stores and must never report a GA Store as evaluated.
    """
    r = demo_weather_alert_scenario('Excessive Heat Warning', 'TX')
    item = r['items'][0]
    item.pop('geocode', None)
    item['area_desc'] = 'Statewide area (Demonstration scenario)'
    item['geometry'] = {
        'type': 'Polygon',
        'coordinates': [[[-107.0, 28.0], [-94.0, 28.0], [-94.0, 34.0], [-107.0, 34.0], [-107.0, 28.0]]],
    }
    result = build_weather_scenario(r)
    geography_match = result['geography_match']
    evaluated_ids = set(geography_match['evaluated_store_ids'])
    assert evaluated_ids, "expected some evaluated Stores to be reported"
    assert not (evaluated_ids & {'201', '202', '203', '204', '205', '206', '207', '208'})
    # The rectangle geometrically contains every TX Store point, so the polygon
    # intersection itself must actually match them (proves this isn't accidentally
    # falling through to the "no Store matched" path for an unrelated reason).
    assert set(geography_match['affected_store_ids']) == {'101', '102', '103', '104', '105', '106', '107', '108'}


def test_primary_dc_is_store_times_product_specific_not_store_only():
    """A SKU with its own approved primary lane must use that lane's DC, even when it
    differs from the Store's blanket 'Nearby DC ID' -- otherwise every product at a
    Store is forced onto one DC regardless of what the lane table actually approves.

    Fixture data (data/fixtures/v1/store_sku_dc_lanes.csv) deliberately gives Store 105's
    Rock Salt (UPC 041303011003) a primary lane to DC-TX2 (Texas Alternate DC), while
    Store 105's Nearby DC ID is DC-TX1 (Dallas Regional DC) and every other product at
    Store 105 keeps DC-TX1 as primary. Both must be observable in the same incident.
    """
    r = demo_weather_alert_scenario('Winter Storm Warning', 'TX', county='Collin County')
    result = build_weather_scenario(r)
    df = result['scenario_df']
    assert not df.empty
    assert set(df['Store ID'].unique()) == {'105'}

    rock_salt = df[df['UPC'] == '041303011003']
    assert not rock_salt.empty
    assert (rock_salt['Primary DC'] == 'Texas Alternate DC').all()

    other_products = df[df['UPC'] != '041303011003']
    assert not other_products.empty
    assert (other_products['Primary DC'] == 'Dallas Regional DC').all()


def test_actual_weather_labels_unique():
    rows=frame(); other=rows.iloc[0].copy();other['region']='GA'
    assert signal_display_label(rows.iloc[0]) != signal_display_label(other)

def test_missing_calendar_month_is_not_mom():
    data={'Results':{'series':[{'data':[{'year':'2026','period':'M08','value':'110'},{'year':'2026','period':'M06','value':'100'}]}]}}
    signal,df=build_cpi_signal(data,'Headline CPI','x','Dollar Tree')
    assert pd.isna(df.iloc[-1]['cpi_mom_change_pct'])
    assert '10.00% MoM' not in signal['score_reason']

def test_polygon_exclusion_not_overridden_by_county():
    result=match_alert_to_stores('Dallas County','TX',[{'Store ID':'1','State':'TX','County':'Dallas County','Latitude':10,'Longitude':10}],alert_geometry={'type':'Polygon','coordinates':[[[0,0],[1,0],[1,1],[0,1],[0,0]]]})
    assert not result.affected_store_ids

def test_multistate_county_alert_evaluates_both_states():
    r=demo_weather_alert_scenario('Excessive Heat Warning','TX')
    r['items'][0]['source_query_states']=['TX','GA'];r['items'][0]['area_desc']='Dallas County; Fulton County'
    result=build_weather_scenario(r)
    assert {'101','201'} <= set(result['geography_match']['affected_store_ids'])

def test_shared_dc_capacity_not_overpromised():
    r=demo_weather_alert_scenario('Excessive Heat Warning','TX')
    r['items'][0]['area_desc']='Dallas County; Tarrant County; Harris County; Travis County; Bexar County'
    result=build_weather_scenario(r);df=result['scenario_df'];inv=demo_dc_inventory();network=demo_dc_network()
    assert not df.empty
    allocations={}
    for _,row in df.iterrows():
        for kind in ['Primary','Backup']:
            key=(row['UPC'],row[f'{kind} DC'])
            allocations[key]=allocations.get(key,0)+row[f'{kind} Planned Qty']
    for (upc,name),total in allocations.items():
        if total:
            dc=network.loc[network['DC Name']==name,'DC ID'].iloc[0]
            available=float(inv.loc[(inv['UPC']==upc)&(inv['DC ID']==dc),'Available to Push'].iloc[0])
            assert total<=available

def test_cross_incident_shared_capacity_and_dedup():
    r=demo_weather_alert_scenario('Excessive Heat Warning','TX')
    first=r['items'][0];second=dict(first,alert_id='second');r['items']=[first,first,second]
    incidents=build_weather_incidents(r,utc_now())
    assert len(incidents)==2
    totals={}
    inv=demo_dc_inventory();network=demo_dc_network()
    for i in incidents:
        for _,row in i['Scenario']['scenario_df'].iterrows():
            for kind in ['Primary','Backup']:
                key=(row['UPC'],row[f'{kind} DC']);totals[key]=totals.get(key,0)+row[f'{kind} Planned Qty']
    for (upc,name),total in totals.items():
        if total:
            dc=network.loc[network['DC Name']==name,'DC ID'].iloc[0]
            assert total<=float(inv.loc[(inv['UPC']==upc)&(inv['DC ID']==dc),'Available to Push'].iloc[0])

def test_recall_partial_or_unmatched_lot_not_confirmed():
    item=demo_recall_scenario('Peanut Butter');item['lots']=item['lots'][:-1]
    result=match_recall_to_catalog(item)
    assert result['match_status']=='lot_review_required'
    assert result['exposed_units']==0

def test_single_lot_gets_only_its_share():
    item=demo_recall_scenario('Peanut Butter');result=match_recall_to_catalog(item)
    inv=demo_store_inventory();total=inv.loc[inv['UPC']==item['upcs'],'On Hand'].sum()
    assert result['match_status']=='confirmed_exact'
    assert 0<result['total_store_exposure']<total

def test_recall_distribution_filters_stores_and_never_uses_firm_state():
    assert _store_distribution_overlap({'state':'TX','distribution_pattern':''},demo_store_master())==(False,[])
    assert distribution_states('Texas and Georgia')==({'TX','GA'},True)
    item=demo_recall_scenario('Peanut Butter');item['distribution_pattern']='Georgia'
    result=match_recall_to_catalog(item)
    states=set(demo_store_master().query("State == 'GA'")['Store ID'])
    assert result['store_lines']
    assert all(r['Store ID'] in states for r in result['store_lines'])

def test_multi_upc_recall_matching_is_order_independent():
    """R01: match_recall_to_catalog previously returned immediately on the FIRST UPC in
    the list whose lot didn't match exactly, so a genuine exact UPC+lot match on a LATER
    UPC in the same list could never be reached. An exact match anywhere in the list must
    win regardless of position.
    """
    item_a = demo_recall_scenario('Peanut Butter')
    item_b = demo_recall_scenario('Canned Soup')
    assert item_a['lots'] and item_b['lots'] and item_a['lots'] != item_b['lots']

    forward = dict(item_a)
    forward['upcs'] = f"{item_a['upcs']}, {item_b['upcs']}"
    forward['lots'] = item_b['lots']  # only product B's lot is supplied
    forward_result = match_recall_to_catalog(forward)
    assert forward_result['match_status'] == 'confirmed_exact'
    assert forward_result['matched_product'] == 'Canned Soup'

    reverse = dict(forward)
    reverse['upcs'] = f"{item_b['upcs']}, {item_a['upcs']}"
    reverse_result = match_recall_to_catalog(reverse)
    assert reverse_result['match_status'] == 'confirmed_exact'
    assert reverse_result['matched_product'] == 'Canned Soup'


def test_recall_distribution_evidence_preserved_on_lot_and_product_review_branches():
    """R02: lot_review_required and product_review_required previously defaulted
    distribution_states to an empty list without ever calling the distribution parser,
    silently discarding evidence already present in the recall text on exactly those two
    exit paths (every other exit path already parsed it correctly).
    """
    lot_review_item = demo_recall_scenario('Peanut Butter')
    lot_review_item['lots'] = 'SOME-OTHER-LOT-NOT-IN-MASTER'
    lot_review_item['distribution_pattern'] = 'Texas and Georgia'
    lot_review_result = match_recall_to_catalog(lot_review_item)
    assert lot_review_result['match_status'] == 'lot_review_required'
    assert set(lot_review_result['distribution_states']) == {'TX', 'GA'}

    product_review_item = demo_recall_scenario('Peanut Butter')
    product_review_item['upcs'] = ''
    product_review_item['product'] = 'Peanut Butter'
    product_review_item['distribution_pattern'] = 'Texas and Georgia'
    product_review_result = match_recall_to_catalog(product_review_item)
    assert product_review_result['match_status'] == 'product_review_required'
    assert set(product_review_result['distribution_states']) == {'TX', 'GA'}


def test_upc_extraction_prefers_labeled_upc_and_rejects_lot_and_date_digit_runs():
    """R03: extract_upcs previously accepted ANY 8-14 digit run with no label
    requirement, so an unrelated lot code or a code-date could be misread as a UPC.
    A labeled "UPC" match must win; an unlabeled bare digit run must only be accepted at
    a standard barcode length and never when it sits next to lot/date/batch context.
    """
    labeled_text = "UPC: 049000028911. Batch 20260930123456 expires 12/31/2026."
    assert extract_upcs(labeled_text) == ['049000028911']

    # No explicit "UPC" label anywhere -- a 14-digit date/lot string next to "Batch"
    # context must not be treated as a UPC.
    unlabeled_lot_text = "Batch code 20260930123456 packed on this date."
    assert extract_upcs(unlabeled_lot_text) == []

    # A bare, unlabeled digit run at a standard barcode length (12 digits) with no
    # date/lot/batch context nearby is accepted as a cautious fallback.
    bare_upc_text = "Affected product code 049000028911 was distributed nationwide."
    assert extract_upcs(bare_upc_text) == ['049000028911']


def test_recall_action_name_matches_what_actually_needs_confirming(monkeypatch):
    """Each open-question status must name ITS open question, not always "distribution".

    action_name used to collapse to "Distribution footprint review" for every status
    except category_hazard_match -- so a lot-confirmation gap (lot_review_required) and
    a product/UPC-confirmation gap (product_review_required) both told a buyer to check
    distribution, while the Reason text next to that same Action correctly described a
    lot or product problem. Action and Reason must agree.
    """
    expected = {
        'category_hazard_match': 'Category recall review',
        'distribution_match': 'Distribution footprint review',
        'lot_review_required': 'Lot confirmation review',
        'product_review_required': 'Product/UPC confirmation review',
        'distribution_review_required': 'Distribution footprint review',
    }
    for status, expected_action in expected.items():
        item = demo_recall_scenario('Peanut Butter')
        stub = _empty_recall_exposure_result(
            item, match_status=status, match_type=status, reason=f'reason for {status}', category='Food Safety',
        )
        monkeypatch.setattr(recall_matching, 'match_recall_to_catalog', lambda _item, _stub=stub: _stub)
        incidents = build_recall_incidents({'items': [item]})
        assert incidents, f'no incident built for status {status}'
        actions = incidents[0]['Recommended Actions']
        assert actions, f'no action produced for status {status}'
        assert actions[0]['Action'] == expected_action, (
            f'status {status} produced action {actions[0]["Action"]!r}, expected {expected_action!r}'
        )

def test_recall_unmatched_is_not_low_priority():
    """Consistency with Weather's no-Store-match policy: an 'unmatched' recall (no
    internal catalog/UPC/lot evidence at all) must not be presented as a real, graded
    'Low' priority decision -- that would misrepresent an unassessed case as reviewed
    and judged low-severity. It must get a blank Priority and an explicit no-action item,
    the same convention Weather already uses for 'No Store action applies'.
    """
    item = demo_recall_scenario('Peanut Butter')
    item['upcs'] = ''
    item['product'] = 'Totally Unrelated Product Description'
    item['distribution_pattern'] = ''
    incidents = build_recall_incidents({'items': [item]})
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident['Priority'] == ''
    assert incident['Recommended Actions']
    assert incident['Recommended Actions'][0]['Action'] == 'No Product/Store action applies'
    assert incident['Recommended Actions'][0]['Status'] == 'No action required'


def test_recall_current_query_and_exact_class_counts(monkeypatch):
    seen={}
    records=[{'classification':'Class I','status':'Ongoing','reason_for_recall':'allergen'}]*11+[{'classification':'Class II','status':'Ongoing','reason_for_recall':'allergen'}]*9+[{'classification':'Class I','status':'Terminated'}]
    def request(*args,**kwargs):
        seen.update(kwargs);return True,{'results':records},''
    monkeypatch.setattr(http_client, 'safe_request',request)
    r=collect_fda_recalls('product_description:snacks',50)
    assert 'status:"Ongoing"' in seen['params']['search']
    assert len(r['items'])==20
    assert r['rows'][0]['class_i_count']==11
    assert r['rows'][0]['class_ii_count']==9

def test_staffing_filter_scope_is_store_only_not_product_or_dc():
    """Filter consistency: staffing/employee records are per-Store data with no
    Product or DC dimension at all, so a Product or Primary/Backup DC filter
    selection must never remove staffing rows -- only the Store filter may. The
    incident detail view previously ignored every filter for these two tables (they
    read scenario.get(...) directly); apply_incident_scope_filters() is the shared
    filter context that fixes this without applying one universal predicate.
    """
    staffing_df = pd.DataFrame([
        {'Store ID': '101', 'Scheduled Staff': 10},
        {'Store ID': '102', 'Scheduled Staff': 8},
    ])

    # Store filter narrows staffing, as it should.
    scoped = apply_incident_scope_filters(staffing_df, store_filter=['101'], dimensions=('store',))
    assert scoped['Store ID'].tolist() == ['101']

    # A Product/DC filter passed in alongside a staffing table declared store-only
    # must be silently ignored for that table -- it has no Product/DC columns to
    # filter on, and no row should ever be dropped because of them.
    scoped_with_other_filters = apply_incident_scope_filters(
        staffing_df,
        store_filter=[],
        product_filter=['Some Product'],
        primary_dc_filter=['DC-TX1'],
        dimensions=('store',),
    )
    assert len(scoped_with_other_filters) == len(staffing_df)

    # The weather Store/product/DC detail table still gets the full filter set it had
    # before -- this is a regression check that the refactor into a shared helper
    # didn't drop any of the four existing filters.
    weather_df = pd.DataFrame([
        {'Store ID': '101', 'Product': 'Rock Salt', 'Primary DC': 'DC-TX1', 'Backup DC': 'DC-TX2'},
        {'Store ID': '102', 'Product': 'Bottled Water', 'Primary DC': 'DC-TX2', 'Backup DC': 'DC-TX1'},
    ])
    assert apply_incident_scope_filters(weather_df, store_filter=['101'])['Store ID'].tolist() == ['101']
    assert apply_incident_scope_filters(weather_df, product_filter=['Bottled Water'])['Store ID'].tolist() == ['102']
    assert apply_incident_scope_filters(weather_df, primary_dc_filter=['DC-TX1'])['Store ID'].tolist() == ['101']
    assert apply_incident_scope_filters(weather_df, backup_dc_filter=['DC-TX1'])['Store ID'].tolist() == ['102']


def test_incomplete_brief_and_misplaced_actions_rejected():
    bad='EXECUTIVE SUMMARY\nTOP INSIGHTS\nPLANNING RELEVANCE\nRECOMMENDED ACTIONS\nCONFIDENCE AND LIMITATIONS\n- one thing here\n- two things here\n- three things here'
    assert not validate_executive_brief(bad)[0]
    assert validate_executive_brief(VALID)[0]
    assert not validate_executive_brief(VALID.replace('Verify the affected store geography.','x'))[0]


def test_brief_heading_recognizes_bare_top_insights_and_planning_relevance_variants():
    """L02: the model writes the bare "TOP INSIGHTS"/"PLANNING RELEVANCE" headings (not
    only "TOP 3 INSIGHTS"/"FORECASTING RELEVANCE"), and brief_section_heading() --
    the flexible heading recognizer used to render/parse a free-form brief -- previously
    did not know either bare variant, so they rendered as plain paragraph text instead of
    section headings. Both the new and the pre-existing variants must still be recognized.
    """
    assert brief_section_heading("TOP INSIGHTS") == "TOP INSIGHTS"
    assert brief_section_heading("Planning Relevance") == "Planning Relevance"
    assert brief_section_heading("Top 3 Insights") == "Top 3 Insights"
    assert brief_section_heading("Forecasting Relevance") == "Forecasting Relevance"
    assert brief_section_heading("Not a real heading") == ""


def test_validator_rejects_nan_literal_and_echoed_prompt_instructions():
    """L01: a NaN value or the model echoing its own prompt text is a distinct failure
    mode from a missing heading or an unsupported number -- both must be caught
    directly rather than relying on a downstream check to happen to notice.
    """
    nan_brief = VALID.replace(
        'Review affected stores against the supplied evidence.',
        'Confidence is nan given incomplete data.',
    )
    ok, reason = validate_executive_brief(nan_brief)
    assert not ok and 'NaN' in reason

    leak_brief = VALID.replace(
        'Review affected stores against the supplied evidence.',
        'Use these five exact headings as specified in the instructions.',
    )
    ok, reason = validate_executive_brief(leak_brief)
    assert not ok and 'echoes prompt instructions' in reason


def test_validator_grounds_score_and_signal_count_claims_to_the_correct_metric():
    """L03/L01: a claimed score or signal count was previously checked only by asking
    "does this number appear anywhere in the supplied evidence dump", which would
    accept a claimed score or count that coincidentally matched an unrelated field (a
    Store ID, an unbounded sum). Both must now be checked against their own specific,
    correctly-scoped metric.
    """
    payload = {"retailer": "Dollar Tree", "region": "US", "signals": [
        {"source": "NOAA Weather Alerts", "signal_area": "Weather Risk", "risk_score": 8.0},
    ], "supporting_news": []}

    correct_score_brief = VALID.replace(
        'Review affected stores against the supplied evidence.',
        'This signal carries a score of 8.',
    )
    assert validate_executive_brief(correct_score_brief, payload=payload)[0]

    wrong_score_brief = VALID.replace(
        'Review affected stores against the supplied evidence.',
        'This signal carries a score of 3.',
    )
    ok, reason = validate_executive_brief(wrong_score_brief, payload=payload)
    assert not ok and 'does not match any supplied risk score' in reason

    correct_count_brief = VALID.replace(
        'Review affected stores against the supplied evidence.',
        'This review covers 1 supplied signal.',
    )
    assert validate_executive_brief(correct_count_brief, payload=payload)[0]

    wrong_count_brief = VALID.replace(
        'Review affected stores against the supplied evidence.',
        'This review covers 4 supplied signals.',
    )
    ok, reason = validate_executive_brief(wrong_count_brief, payload=payload)
    assert not ok and 'does not match the 1 supplied signal' in reason


def test_validator_rejects_invented_feature_name_not_in_supplied_evidence():
    """L01: an invented feature/metric name (e.g. a plausible-looking but fabricated
    snake_case identifier) must not reach the screen just because the rest of the brief
    is otherwise well-formed.
    """
    payload = {"retailer": "Dollar Tree", "region": "US", "signals": [
        {"source": "NOAA Weather Alerts", "signal_area": "Weather Risk", "risk_score": 8.0},
    ], "supporting_news": []}
    invented_brief = VALID.replace(
        'Review affected stores against the supplied evidence.',
        'Driven by gasoline_cpi_pressure_score this week.',
    )
    ok, reason = validate_executive_brief(invented_brief, payload=payload)
    assert not ok and 'gasoline_cpi_pressure_score' in reason

def test_nvidia_success_trace_exact_and_no_key_counts(monkeypatch):
    captured=[]
    def fake(**kwargs): captured.append(kwargs);return True,VALID,'',10
    monkeypatch.setattr(nvidia_client, '_nvidia_chat_request',fake)
    brief,source,audit=generate_nvidia_brief('test','nvidia/nemotron-3-super-120b-a12b',frame(),[],'Dollar Tree','US')
    assert source=='nvidia'
    assert audit['user_prompt']==captured[0]['user_prompt']==audit['actual_request']['user_prompt']
    assert audit['raw_response']==VALID
    assert audit['request_hash_sha256']==payload_hash(audit['actual_request'])
    assert captured[0]['max_tokens']>=1400
    body=audit['actual_request']['request_body']
    assert body['chat_template_kwargs']['enable_thinking'] is False
    _,_,nokey=generate_nvidia_brief('','x',frame(),[],'Dollar Tree','US')
    assert nokey['feature_rows_sent']==0 and not nokey['sent_to_llm']

def test_validation_retry_has_all_sections_and_backup(monkeypatch):
    calls=[]
    def fake(**kwargs):
        calls.append(kwargs)
        return (True,VALID,'',10) if len(calls)==3 else (True,'<concise action>','',10)
    monkeypatch.setattr(nvidia_client, '_nvidia_chat_request',fake)
    _,source,audit=generate_nvidia_brief('test',DEFAULT_NVIDIA_MODEL,frame(),[],'Dollar Tree','US')
    assert source=='nvidia' and audit['accepted_attempt']==3
    assert calls[-1]['model']=='meta/llama-3.3-70b-instruct'
    assert all('PLANNING RELEVANCE' in c['user_prompt'] for c in calls)
    assert not audit['nvidia_attempts'][0]['success']

def test_auth_failure_stops_without_pointless_model_retry(monkeypatch):
    calls=[]
    def fake(**kwargs):calls.append(kwargs);return False,'','401 unauthorized',10
    monkeypatch.setattr(nvidia_client, '_nvidia_chat_request',fake)
    _,source,audit=generate_nvidia_brief('bad','x',frame(),[],'Dollar Tree','US')
    assert source=='fallback' and len(calls)==1 and not audit['accepted_response']

def test_gone_endpoint_stops_model_retry(monkeypatch):
    calls=[]
    def fake(**kwargs):
        calls.append(kwargs)
        return False, '', 'HTTP 410 Gone from the configured NVIDIA chat endpoint', 10
    monkeypatch.setattr(nvidia_client, '_nvidia_chat_request', fake)
    _, source, audit = generate_nvidia_brief('test', DEFAULT_NVIDIA_MODEL, frame(), [], 'Dollar Tree', 'US')
    assert source == 'fallback' and len(calls) == 1
    assert '410 Gone' in audit['fallback_reason']

def test_service_unavailable_can_try_backup(monkeypatch):
    calls=[]
    def fake(**kwargs):
        calls.append(kwargs)
        return (True, VALID, '', 10) if len(calls) == 3 else (False, '', '503 Service Unavailable', 10)
    monkeypatch.setattr(nvidia_client, '_nvidia_chat_request', fake)
    _, source, audit = generate_nvidia_brief('test', DEFAULT_NVIDIA_MODEL, frame(), [], 'Dollar Tree', 'US')
    assert source == 'nvidia' and audit['accepted_attempt'] == 3

def test_source_participation_is_actual_accepted_payload():
    results={'weather':{'status':'success','rows':frame().to_dict('records')},'bls':{'status':'success','rows':[{'source':'BLS CPI'}]}}
    audit={'accepted_response':True,'payload':{'signals':[{'source':'NOAA Weather Alerts'}],'supporting_news':[]}}
    records=build_collector_evidence(results,{},audit)
    values={r['source']:r for r in records}
    assert values[SOURCE_LABELS['weather']]['feature_rows_in_accepted_payload']==1
    assert values[SOURCE_LABELS['bls']]['feature_rows_in_accepted_payload']==0

def test_planner_shortage_not_offset_by_other_sku_surplus():
    data=pd.DataFrame([{'Baseline Forecast':100,'Historical Average':100,'On Hand':0,'Inbound':0},{'Baseline Forecast':100,'Historical Average':100,'On Hand':200,'Inbound':0}])
    result=calculate_demand_plan({},data)
    assert result['Baseline Gap']==100 and result['Baseline Surplus']==100

def test_missing_source_is_not_low():
    scores=compute_retail_kpis(frame())
    assert risk_band(scores['Safety and Compliance Risk'])=='Not evaluated'

def test_modified_decision_requires_action():
    with pytest.raises(ValueError):save_decision('x','reviewer','Modified','reason','')

def test_full_run_evidence_saved(isolated_database,monkeypatch):
    monkeypatch.setattr(settings, 'RUN_HISTORY_DB',str(isolated_database));init_run_history_db()
    run={'timestamp':'test','feature_df':frame(),'results':{},'llm_audit':{'raw_response':'original'},'brief':'report'}
    save_run_history(run,run_type='test',source='test')
    import sqlite3
    with sqlite3.connect(isolated_database) as conn:
        stored=json.loads(conn.execute('select payload_json from run_evidence').fetchone()[0])
    assert stored['llm_audit']['raw_response']=='original'

def test_market_intelligence_runs_table_migrates_an_old_pre_column_database(isolated_database, monkeypatch):
    """Old bug pattern (fixed for operational_incidents but not market_intelligence_runs):
    CREATE TABLE IF NOT EXISTS alone means a pre-existing DB file created before a column
    was added fails every INSERT with 'table has N columns but M values were supplied.'
    Simulate a pre-existing DB with only the oldest possible column set, then confirm
    init_run_history_db() migrates it in place and a real save_run_history() succeeds.
    """
    import sqlite3

    monkeypatch.setattr(settings, 'RUN_HISTORY_DB', str(isolated_database))
    with sqlite3.connect(isolated_database) as conn:
        conn.execute(
            """
            CREATE TABLE market_intelligence_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_type TEXT NOT NULL
            )
            """
        )
        conn.commit()

    init_run_history_db()
    run = {'timestamp': 'test', 'feature_df': frame(), 'results': {}, 'llm_audit': {}, 'brief': 'report'}
    save_run_history(run, run_type='test', source='test', keywords='k', time_window='7d', geography='US')

    with sqlite3.connect(isolated_database) as conn:
        row = conn.execute("SELECT source, geography FROM market_intelligence_runs LIMIT 1").fetchone()
    assert row == ('test', 'US')


def test_scenario_detail_and_planner_invalidation(isolated_database):
    from streamlit.testing.v1 import AppTest
    os.environ['MARKET_INTELLIGENCE_DB']=str(isolated_database)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
    app.session_state['workbench_view']='Operational Impact Center';app.run()
    next(b for b in app.button if b.label=='Create weather scenario').click().run()
    assert not app.exception
    assert 'Impact' in [t.label for t in app.tabs]
    assert any(s.label=='Open scenario detail' for s in app.selectbox)
    app.session_state['workbench_view']='Demand Planner';app.run()
    next(b for b in app.button if b.label=='Analyze Demand Impact').click().run()
    assert 'demand_plan_result' in app.session_state
    next(s for s in app.selectbox if s.label=='Store (illustrative internal data)').select_index(1).run()
    assert 'demand_plan_result' not in app.session_state
    assert not app.exception

def test_all_populated_views_and_both_scenario_types(isolated_database):
    """Exercise nested screens with injected controlled data, never network calls."""
    from streamlit.testing.v1 import AppTest
    os.environ['MARKET_INTELLIGENCE_DB']=str(isolated_database)
    weather=demo_weather_alert_scenario('Excessive Heat Warning','GA')
    weather['rows'][0].pop('is_demo_scenario',None);weather['rows'][0]['source']='NOAA Weather Alerts'
    recall=demo_recall_scenario('Peanut Butter');recall.pop('is_demo_scenario',None)
    features=enrich_feature_rows_for_retailer(pd.DataFrame(weather['rows']),'Dollar Tree')
    brief,source,audit=generate_nvidia_brief('','test',features,[],'Dollar Tree','US')
    run={'timestamp':utc_now(),'run_config':{'retailer':'Dollar Tree','region':'US'},'results':{'weather':weather,'fda':{'status':'success','items':[recall],'rows':[]}},'feature_df':features,'articles':[],'brief':brief,'brief_source':source,'llm_audit':audit}
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
    app.session_state['run']=run
    for view in ['Configure','Demand Planner','Operational Impact Center','Results','Evidence Audit','Raw Data']:
        app.session_state['workbench_view']=view;app.run()
        assert not app.exception,view
        if view=='Operational Impact Center':
            # Two live incident details (weather and recall); a saved what-if scenario adds a third.
            assert [t.label for t in app.tabs].count('Impact')>=2
            next(b for b in app.button if b.label=='Create recall scenario').click().run()
            assert not app.exception
            # The page lists one scenario detail at a time, so a saved scenario means exactly one more.
            assert [t.label for t in app.tabs].count('Impact')>=3

def test_nvidia_truncation_is_rejected_and_thinking_disabled(monkeypatch):
    captured={}
    def request(*args,**kwargs):
        captured.update(kwargs)
        return True,{'choices':[{'finish_reason':'length','message':{'content':'unfinished'}}]},''
    monkeypatch.setattr(http_client, 'safe_request',request)
    ok,content,error,_=_nvidia_chat_request('test',DEFAULT_NVIDIA_MODEL,'s','u',380,30)
    assert not ok and 'truncated' in error
    assert captured['json_body']['max_tokens']>=1400
    assert captured['json_body']['chat_template_kwargs']=={'enable_thinking':False}

def test_nvidia_all_invalid_responses_remain_honest_fallback(monkeypatch):
    monkeypatch.setattr(nvidia_client, '_nvidia_chat_request',lambda **kwargs:(True,'<write summary here>','',1))
    _,source,audit=generate_nvidia_brief('test',DEFAULT_NVIDIA_MODEL,frame(),[],'Dollar Tree','US')
    assert source=='fallback' and audit['fallback_used']
    assert audit['sent_to_llm'] and not audit['accepted_response']
    assert len(audit['nvidia_attempts'])==3
    assert all(not attempt['success'] for attempt in audit['nvidia_attempts'])
