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
