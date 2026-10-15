"""Review points of 14 Oct: honest wording, weather priority, export identity and empty FDA search."""
import pandas as pd

from market_intelligence.collectors.live_noaa import collect_weather_alerts
from market_intelligence.data.demo import demo_store_master
from market_intelligence.infra import http as http_client
from market_intelligence.signals.labels import client_feature_table
from market_intelligence.signals.retail_context import enrich_feature_rows_for_retailer, internal_exposure_for_signal, retail_context_for_signal
from market_intelligence.signals.scoring import build_trust_metrics, retail_kpi_driver


def _weather(matched, screened=True, score=8.25):
    return {"source": "NOAA Weather Alerts", "signal_area": "Weather Risk", "signal_name": "supply_chain_weather_risk_score",
            "region": "GA", "risk_score": score, "fetched_count": 2, "operationally_relevant_fetched_count": matched,
            "store_screening_applied": screened}


def test_state_weather_without_store_match_has_blank_priority_like_the_impact_center():
    context = retail_context_for_signal(_weather(0))
    assert context["action_priority"] == ""
    assert "No configured-store county match" in context["internal_exposure"]
    assert retail_context_for_signal(_weather(1))["action_priority"] == "High"
    # Without screening the priority is not changed on a count that was never checked.
    assert retail_context_for_signal(_weather(0, screened=False))["action_priority"] == "High"
    # A state with no alerts at all keeps its normal priority and says so.
    quiet = dict(_weather(0, score=0.0), fetched_count=0)
    assert retail_context_for_signal(quiet)["action_priority"] == "Monitor"
    assert retail_context_for_signal(quiet)["internal_exposure"] == "No active alerts"


def test_weather_card_next_step_follows_the_store_match():
    from market_intelligence.signals.scoring import build_recommended_actions

    unmatched = build_recommended_actions(pd.DataFrame([_weather(0)]), {})[0]["body"]
    assert "No store action needed" in unmatched and "affected store" not in unmatched
    matched = build_recommended_actions(pd.DataFrame([_weather(1)]), {})[0]["body"]
    assert "Review the matched store(s)" in matched


def test_recall_and_market_signals_state_their_internal_exposure():
    assert "Operational Impact Center" in internal_exposure_for_signal({"signal_area": "Product Recalls", "source": "openFDA"})
    assert internal_exposure_for_signal({"signal_area": "Inflation", "source": "BLS CPI"}).startswith("Not applicable")


def test_export_carries_run_id_retailer_and_numeric_score():
    rows = enrich_feature_rows_for_retailer(
        pd.DataFrame([dict(_weather(0), retailer="Dollar Tree", date="2026-10-14", confidence="High")]), "Dollar Tree"
    )
    table = client_feature_table(rows, for_export=True, run_date="2026-10-14", run_id="2026-10-14T09:36:00Z")
    assert list(table.columns[:2]) == ["Run ID", "Retailer"]
    assert table.loc[0, "Retailer"] == "Dollar Tree" and table.loc[0, "Risk Score"] == 8.25
    assert table.loc[0, "Risk Level"] == "High" and table.loc[0, "Data Period"] == "Active as of run time"
    assert "Source Confidence" in table.columns and "Internal Exposure" in table.columns
    # The on-screen table is unchanged apart from the new exposure column.
    display = client_feature_table(rows, run_date="2026-10-14")
    assert "Run ID" not in display.columns and "Risk Score" not in display.columns


def test_noaa_reason_separates_fetched_and_retained(monkeypatch):
    features = [
        {"id": str(i), "properties": {"event": "High Wind Watch", "severity": "Severe", "urgency": "Expected",
                                      "certainty": "Likely", "areaDesc": "Nowhere County, ZZ"}}
        for i in range(8)
    ]
    monkeypatch.setattr(http_client, "safe_request", lambda *a, **k: (True, {"features": features}, "success"))
    row = collect_weather_alerts("TX", limit=5, store_master=demo_store_master())["rows"][0]
    assert row["raw_reference"].startswith("8 active NOAA alerts fetched; 5 retained for scoring; top retained event:")
    assert "NOAA returned 8 active TX alerts; 5 were retained for scoring" in row["score_reason"]
    assert "0 of the 8 fetched alerts were in a configured store county" in row["score_reason"]
    assert "5 of the 5 retained were severe or extreme" in row["score_reason"]
    assert "fetched alert(s) were" not in row["score_reason"]
    assert row["store_screening_applied"] is True


def test_data_notice_and_kpi_driver_are_plain():
    notice = next(m for m in build_trust_metrics({"results": {}, "run_config": {}, "llm_audit": {}}, pd.DataFrame())
                  if m["label"] == "Internal Operations")
    assert "illustrative test data" in notice["note"] and "Versioned internal dataset" not in notice["note"]
    rows = pd.DataFrame([
        {"source": "BLS CPI", "signal_area": "Category CPI", "signal_name": "gasoline_cpi_pressure_score", "region": "US", "risk_score": 10.0},
        {"source": "BLS CPI", "signal_area": "Inflation", "signal_name": "inflation_pressure_score", "region": "US", "risk_score": 5.6},
    ])
    assert retail_kpi_driver(rows, "Consumer Price Pressure") == "Gasoline CPI"


def test_no_store_match_weather_rows_have_blank_priority_and_monitor_actionability(isolated_database, monkeypatch):
    """Operational Impact Center: no Store in the alert area -> nothing to rank (blank priority),
    but the alert is still watched (Actionability "Monitor"); the Priority column stays visible."""
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from market_intelligence.application import run_intelligence as ri

    alerts = [{"id": "x1", "properties": {"event": "Flood Warning", "severity": "Severe", "urgency": "Immediate",
                                          "certainty": "Observed", "areaDesc": "Nowhere County, ZZ"}}]
    monkeypatch.setattr(http_client, "safe_request", lambda *a, **k: (True, {"features": alerts}, "success"))
    monkeypatch.setattr(ri, "collect_gnews", lambda *a, **k: {"status": "empty", "rows": [], "items": []})
    monkeypatch.setattr(ri, "collect_bls_cpi", lambda *a, **k: {"status": "success", "rows": [
        {"source": "BLS CPI", "signal_area": "Inflation", "signal_name": "inflation_pressure_score", "risk_score": 5.0,
         "score_reason": "r", "region": "US"}], "items": []})
    monkeypatch.setattr(ri, "collect_fda_recalls", lambda *a, **k: {"status": "empty", "rows": [], "items": []})

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=90)
    app.run()
    next(b for b in app.sidebar.button if b.label == "Run intelligence").click().run()
    assert not app.exception, app.exception
    nav = app.button_group[0] if app.button_group else (app.segmented_control[0] if getattr(app, "segmented_control", None) else app.radio[0])
    nav.set_value("Operational Impact Center").run()
    assert not app.exception, app.exception
    inbox = next(frame.value for frame in app.dataframe
                 if hasattr(frame.value, "columns") and "Actionability" in getattr(frame.value, "columns", []))
    frame = getattr(inbox, "data", inbox)
    assert "Operational Priority" in frame.columns
    assert (frame["Operational Priority"].astype(str).str.strip() == "").all()
    assert (frame["Actionability"] == "Monitor").all()


def test_recall_priority_is_set_only_once_the_upc_matches():
    """Recall counterpart of the weather rule: no internal UPC match -> blank priority."""
    from market_intelligence.incidents.builders import build_recall_incidents

    def priority(**item):
        base = {"recall_number": "F-P-" + item.get("product", "")[:8], "upcs": "", "lots": "", "reason": "Salmonella",
                "distribution_pattern": "GA, TX", "classification": "Class I"}
        base.update(item)
        return build_recall_incidents({"status": "success", "items": [base]}, "run")[0]

    assert priority(product="Fresh Salsa, 16 oz tub")["Priority"] == ""            # product-name match only
    assert priority(product="Acme Salsa Roja Hot 16 oz")["Priority"] == ""        # product-family match only
    assert priority(product="Fresh Salsa, 16 oz tub", distribution_pattern="New York.")["Priority"] == ""
    # UPC matched and FDA lists no lot codes: every lot of the UPC is affected -> full exposure.
    all_lots = priority(product="Marketside Tomato Bisque Soup Kit", upcs="194346474004")
    assert all_lots["Status"] == "confirmed_exact" and all_lots["Priority"] == "High"
    assert all_lots["Match"]["all_lots_assumed"] is True and all_lots["Store IDs"]
    # UPC matched but the recalled lots are not ours: not affected, no priority, no store action.
    not_ours = priority(product="Marketside Tomato Bisque Soup Kit", upcs="194346474004", lots="ZZ-NOT-OURS")
    assert not_ours["Status"] == "lot_not_in_stock" and not_ours["Priority"] == ""
    assert not_ours["Impact Metrics"]["UPC/Lot-Confirmed Affected Stores"] == 0
    assert not_ours["Recommended Actions"][0]["Action"] == "No Product/Store action applies"


def test_active_alert_commute_is_never_assumed_to_beat_the_weather():
    from market_intelligence.weather.staffing import evaluate_staffing_exposure

    store = demo_store_master().iloc[0]
    active = evaluate_staffing_exposure(store, "Somewhere County", str(store["State"]), alert_is_active=True, hours_until_ends=30.0)
    assessments = [row["Commute Assessment"] for row in active["employee_detail"]]
    assert assessments and not any("estimated to arrive before conditions worsen" in text for text in assessments)
    measured = [text for text in assessments if "estimated commute" in text]
    assert measured and all("alert already active, commute requires confirmation" in text for text in measured)
    upcoming = evaluate_staffing_exposure(store, "Somewhere County", str(store["State"]), hours_until_event=30.0)
    assert any("estimated to arrive before conditions worsen" in row["Commute Assessment"] for row in upcoming["employee_detail"])


def test_closed_store_in_alert_area_is_not_called_no_store_match():
    from market_intelligence.weather.inbox_helpers import _weather_store_ids_from_row, weather_actionability, weather_recommended_action

    row = {"Operational Priority": "", "Store IDs": "", "Closed Store IDs": "108",
           "Store Exposure": "1 Store matched · closed · 108 (El Paso, TX)", "Store Match": "NOAA UGC/FIPS join"}
    assert _weather_store_ids_from_row(row) == []
    assert weather_actionability(row) == "Monitor"
    action = weather_recommended_action(row)
    assert "Store 108 is inside the alert area but closed" in action and "no Store matched" not in action
    assert "no Store matched" in weather_recommended_action(dict(row, **{"Closed Store IDs": "", "Store Exposure": "No Store match · 8 evaluated"}))


def test_demand_planner_uses_the_stores_an_alert_actually_reached():
    from market_intelligence.ui.views.demand_planner import _weather_reached_store_ids

    run = {"operational_incidents_snapshot": [
        {"Type": "Weather", "Scenario": {"state": "TX"}, "Store IDs": ["104"]},
        {"Type": "Weather", "Scenario": {"state": "TX"}, "Store IDs": []},
        {"Type": "Weather", "Scenario": {"state": "GA"}, "Store IDs": ["201"]},
    ]}
    assert _weather_reached_store_ids(run, {"Signal Area": "Weather Risk", "Region": "TX"}) == {"104"}
    assert _weather_reached_store_ids({"operational_incidents_snapshot": []}, {"Signal Area": "Weather Risk", "Region": "GA"}) == set()
    assert _weather_reached_store_ids(run, {"Signal Area": "Inflation", "Region": "US"}) is None
    assert _weather_reached_store_ids(None, {"Signal Area": "Weather Risk", "Region": "TX"}) is None


def test_configure_says_a_non_us_news_market_does_not_change_us_sources(isolated_database):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=60)
    app.run()
    text = lambda: " ".join(m.value for m in app.markdown)
    assert "News market:" not in text()
    next(box for box in app.selectbox if box.label == "News country").set_value("GB").run()
    assert not app.exception, app.exception
    assert "News market: GB. Inflation CPI (BLS), FDA recalls and NOAA weather remain US sources" in text()


def test_filters_have_labels_and_readable_options():
    from market_intelligence.ui.views.operational_impact.inbox import FILTER_LABELS, short_filter_option

    assert short_filter_option("Not assessed - no Store match") == "No store match"
    assert short_filter_option("Not assessed - matched Store(s) closed") == "Matched store closed"
    assert short_filter_option("Not assessed - alert expired") == "Alert expired"
    assert short_filter_option("Exact Scenario Match") == "Exact Scenario Match"
    assert short_filter_option("All demand") == "All demand"
    assert FILTER_LABELS["Operational Priority"] == "Priority" and FILTER_LABELS["Demand Basis"] == "Demand basis"


def test_news_window_defaults_to_one_month_and_bls_month_is_named():
    from pathlib import Path
    from market_intelligence.ui.views.configure import _latest_cpi_month

    root = Path(__file__).resolve().parents[1]
    assert 'setdefault("gnews_period", "30d")' in (root / "market_intelligence/ui/bootstrap.py").read_text()
    assert '"7d"' not in (root / "market_intelligence/ui/views/run_pipeline.py").read_text()
    run = {"feature_df": pd.DataFrame([
        {"signal_area": "Inflation", "date": "2026-09"},
        {"signal_area": "Category CPI", "date": "2026-08"},
        {"signal_area": "Weather Risk", "date": "2026-10-15"},
    ])}
    assert _latest_cpi_month(run) == "Sep 2026"
    assert _latest_cpi_month(None) == "" and _latest_cpi_month({"feature_df": pd.DataFrame()}) == ""


def test_incident_inbox_has_labelled_filters_and_inline_detail(isolated_database, monkeypatch):
    """The weather inbox shows labelled filters with short option text, and the detail stays below the table."""
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from market_intelligence.application import run_intelligence as ri

    alerts = [{"id": "x1", "properties": {"event": "Flood Warning", "severity": "Severe", "urgency": "Immediate",
                                          "certainty": "Observed", "areaDesc": "Nowhere County, ZZ"}}]
    monkeypatch.setattr(http_client, "safe_request", lambda *a, **k: (True, {"features": alerts}, "success"))
    monkeypatch.setattr(ri, "collect_gnews", lambda *a, **k: {"status": "empty", "rows": [], "items": []})
    monkeypatch.setattr(ri, "collect_bls_cpi", lambda *a, **k: {"status": "empty", "rows": [], "items": []})
    monkeypatch.setattr(ri, "collect_fda_recalls", lambda *a, **k: {"status": "empty", "rows": [], "items": []})
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=90)
    app.run()
    next(b for b in app.sidebar.button if b.label == "Run intelligence").click().run()
    nav = app.button_group[0] if app.button_group else (app.segmented_control[0] if getattr(app, "segmented_control", None) else app.radio[0])
    nav.set_value("Operational Impact Center").run()
    assert not app.exception, app.exception
    labels = {box.label for box in app.selectbox}
    assert {"Priority", "Event type", "Action", "Demand basis"} <= labels
    demand = next(box for box in app.selectbox if box.label == "Demand basis")
    assert "No store match" in demand.options and not any(o.startswith("Not assessed") for o in demand.options)
    captions = " ".join(c.value for c in app.caption)
    assert "pop-up" not in captions
    assert "Showing the highest-priority incident. Click a row to open another." in captions


def test_selection_summary_names_the_incident_and_store_for_every_section():
    from market_intelligence.ui.views.operational_impact.incident_detail import weather_selection_summary

    scenario_df = pd.DataFrame([
        {"Store ID": "201", "Store Name": "Store 201 - Atlanta, GA", "Product": "Pet Food"},
        {"Store ID": "202", "Store Name": "Store 202 - Savannah, GA", "Product": "Paper Towels"},
    ])
    incident = {"Event": "Air Quality Alert", "Incident ID": "WX-7EB62A0A4EEB"}
    assert weather_selection_summary(incident, scenario_df, [], [], [], []) == (
        "Air Quality Alert · WX-7EB62A0A4EEB", "All stores · All products · All DCs")
    label, scope = weather_selection_summary(incident, scenario_df, ["201"], ["Pet Food"], [], ["Southeast Alternate DC"])
    assert scope == "Store 201 - Atlanta, GA · Pet Food · Backup DC: Southeast Alternate DC"


def test_cpi_month_note_explains_the_bls_delay():
    from market_intelligence.signals.labels import cpi_month_note

    note = cpi_month_note("Sep 2026")
    assert note.startswith("CPI data period: Sep 2026, the newest month BLS has published.")
    assert "about two weeks after that month ends" in note and "?" not in note
    assert "?" not in cpi_month_note("")


def test_bls_note_sits_in_collector_tuning_not_in_the_data_sources_card(isolated_database):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=60)
    app.run()
    assert not app.exception
    assert any(c.value.startswith("CPI data period:") for c in app.caption)
    assert not any("CPI data period" in m.value for m in app.markdown)


def test_recall_reason_words_do_not_map_a_recall_to_household():
    from market_intelligence.recalls.matching import recall_category_from_text

    colour = {"product": "Honeyville YELLOW COLOR 1 GALLON", "reason": "Undeclared Red #40."}
    assert recall_category_from_text(colour) != "Household"
    mislabel = {"product": "Salsa Roja Medium", "reason": "Misbranded: mislabel of allergen statement"}
    assert recall_category_from_text(mislabel) == "Food / Consumables"   # allergen is a food word
    assert recall_category_from_text({"product": "Laundry detergent pods", "reason": "Packaging defect"}) == "Household"


def test_demand_planner_uses_the_impact_center_categories_for_a_reached_store():
    from market_intelligence.demand.planning import calculate_demand_plan, signal_category_scope
    from market_intelligence.data.demo import demo_store_inventory
    from market_intelligence.ui.views.demand_planner import _incident_categories_for_store

    run = {"operational_incidents_snapshot": [{
        "Type": "Weather", "Store IDs": ["201"], "Scenario": {"state": "GA", "scenario_df": [
            {"Store ID": "201", "Category": "Household", "Product": "Pet Food"},
            {"Store ID": "202", "Category": "Personal Care", "Product": "Shampoo"},
        ]},
    }]}
    signal = {"Signal Area": "Weather Risk", "Region": "GA", "Top Event": "Air Quality Alert"}
    assert signal_category_scope(signal)[0] == ("Personal Care", "Household")   # broad list when no store reached
    categories = _incident_categories_for_store(run, signal, "201")
    assert categories == ["Household"]
    scoped = dict(signal, **{"Scope Categories Override": categories, "Scope Basis Override": "Same as the Impact Center."})
    inventory = demo_store_inventory()
    plan = calculate_demand_plan(scoped, inventory[inventory["Store ID"] == "201"], "Store 201")
    assert plan["Category Scope"] == "Household" and plan["Products In Scope"] == 3
    assert _incident_categories_for_store(run, signal, "203") == []


def _weather_brief_rows():
    return pd.DataFrame([
        {"source": "NOAA Weather Alerts", "signal_area": "Weather Risk", "signal_name": "supply_chain_weather_risk_score",
         "region": "GA", "risk_score": 8.2, "confidence": "High", "score_reason": "Severe alert in GA.",
         "fetched_count": 2, "operationally_relevant_fetched_count": 1, "store_screening_applied": True,
         "business_impact": "Weather can disrupt stores.", "recommended_action": "Review the matched store."},
        {"source": "BLS CPI", "signal_area": "Inflation", "signal_name": "inflation_pressure_score", "region": "US",
         "risk_score": 6.0, "confidence": "High", "score_reason": "CPI pressure.", "business_impact": "Basket pressure.",
         "recommended_action": "Review value categories."},
    ])


def test_weather_payload_carries_the_screening_note():
    from market_intelligence.llm.prompts import WEATHER_STORE_SCREENING_NOTE, _compact_signal_for_nvidia

    rows = _weather_brief_rows()
    assert _compact_signal_for_nvidia(rows.iloc[0])["weather_store_screening_note"] == WEATHER_STORE_SCREENING_NOTE
    assert "weather_store_screening_note" not in _compact_signal_for_nvidia(rows.iloc[1])


def test_accepted_weather_brief_says_the_match_is_preliminary_and_synthetic(monkeypatch):
    from market_intelligence.infra import nvidia as nvidia_client
    from market_intelligence.llm.brief import generate_fallback_brief, generate_nvidia_brief

    rows = _weather_brief_rows()
    # The agent mentions the store-county match but leaves out what it means.
    agent_text = (
        "EXECUTIVE SUMMARY\nGA weather and inflation need attention.\n\n"
        "TOP INSIGHTS\n- GA Weather Risk from NOAA Weather Alerts is High; one alert matched a store county.\n"
        "- Inflation from BLS CPI is Medium.\n\n"
        "PLANNING RELEVANCE\n- External signals are not validated internal impact.\n\n"
        "RECOMMENDED ACTIONS\n- Review the matched store and its routes.\n- Verify DC inventory for essentials.\n"
        "- Monitor the alert before order cut-off.\n\n"
        "CONFIDENCE AND LIMITATIONS\nConfidence varies by source: NOAA High, BLS High. "
        "Operational impact is unconfirmed until signals are joined to internal store, inventory and sales data."
    )
    monkeypatch.setattr(nvidia_client, "_nvidia_chat_request", lambda **kwargs: (True, agent_text, "", 10))
    brief, source, audit = generate_nvidia_brief("test-key", "test-model", rows, [], "Dollar Tree", "US")
    assert source == "nvidia" and audit["accepted_response"]
    assert "synthetic" in brief.lower() and "preliminary" in brief.lower()
    assert "operationally_relevant_fetched_count is above zero, it does not confirm" in audit["user_prompt"]
    fallback = generate_fallback_brief(rows, "Dollar Tree", "US").lower()
    assert "synthetic" in fallback and "preliminary" in fallback
