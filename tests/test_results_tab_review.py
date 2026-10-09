"""Results-tab review fixes: consistent levels, no row-number labels, tied top signals,
state-aware weather wording, level-based movement, relevance-ranked supporting news."""
import pandas as pd

from market_intelligence.llm.brief import generate_fallback_brief, rank_articles_for_signals
from market_intelligence.signals.labels import CANDIDATE_VALIDATION_STATUS, client_feature_table
from market_intelligence.signals.retail_context import signal_display_label, top_signals_summary
from market_intelligence.signals.risk import risk_band
from market_intelligence.signals.scoring import (
    build_previous_run_comparison,
    build_recommended_actions,
    compute_retail_kpis,
    weather_exposure_note,
)


def _rows():
    return pd.DataFrame([
        {"source": "BLS CPI", "signal_area": "Inflation", "signal_name": "inflation_pressure_score", "region": "US", "risk_score": 6.0, "confidence": "High"},
        {"source": "BLS CPI", "signal_area": "Category CPI", "signal_name": "gasoline_cpi_pressure_score", "region": "US", "risk_score": 8.5, "confidence": "High"},
        {"source": "openFDA", "signal_area": "Product Recalls", "signal_name": "recall_risk_score", "region": "US", "risk_score": 9.0, "confidence": "High"},
        {"source": "NOAA Weather Alerts", "signal_area": "Weather Risk", "signal_name": "supply_chain_weather_risk_score", "region": "GA", "risk_score": 8.2, "confidence": "High",
         "fetched_count": 4, "operationally_relevant_fetched_count": 0},
        {"source": "NOAA Weather Alerts", "signal_area": "Weather Risk", "signal_name": "supply_chain_weather_risk_score", "region": "TX", "risk_score": 6.0, "confidence": "High",
         "fetched_count": 2, "operationally_relevant_fetched_count": 0},
        {"source": "GNews", "signal_area": "Retail News", "signal_name": "news_risk_score", "region": "US", "risk_score": 6.0, "confidence": "Medium"},
    ])


def test_kpi_cards_use_clear_names():
    kpis = compute_retail_kpis(_rows())
    assert "Value Basket Pressure" not in kpis
    assert risk_band(kpis["Consumer Price Pressure"]) == "High"  # gasoline drives the combined card
    assert "Supply Chain Disruption Risk" in kpis


def test_chart_labels_have_no_row_numbers():
    rows = _rows()
    labels = [signal_display_label(row, i) for i, (_, row) in enumerate(rows.iterrows())]
    assert not any(label.rstrip().split("·")[-1].strip().isdigit() for label in labels)
    assert "GA · Supply Chain Weather Risk Score" in labels


def test_tied_top_signals_are_all_listed_with_state():
    text, level = top_signals_summary(_rows())
    assert level == "High"
    assert "Product Recalls" in text and "GA Weather Risk" in text and "Gasoline CPI" in text
    assert "TX" not in text  # TX is Medium


def test_weather_card_names_state_and_separates_exposure():
    cards = build_recommended_actions(_rows(), {})
    ga = next(card for card in cards if "GA" in card["title"])
    assert "DC and route exposure is not evaluated" in ga["body"]
    assert "no store or DC exposure" not in ga["body"].lower()
    assert "state-level" in ga["body"].lower()
    assert "matched a store county" in weather_exposure_note(_rows().iloc[3])


def test_movement_follows_visible_level():
    current = _rows()
    previous = current.copy()
    previous["risk_score"] = previous["risk_score"] - 0.6  # small hidden change
    previous.loc[previous["signal_area"] == "Inflation", "risk_score"] = 5.2  # Medium -> Medium
    previous.loc[previous["signal_name"] == "recall_risk_score", "risk_score"] = 4.0  # Low -> High
    frame = build_previous_run_comparison(current, {"feature_df": previous, "run_config": {}})
    move = {(r.signal_name, r.region): r.movement for r in frame.itertuples()}
    assert move[("inflation_pressure_score", "US")] == "Stable"
    assert move[("recall_risk_score", "US")] == "Increased"


def test_export_is_labelled_candidate_only():
    export = client_feature_table(_rows(), for_export=True)
    assert (export["Validation Status"] == CANDIDATE_VALIDATION_STATUS).all()


def test_supporting_news_is_ranked_by_relevance_not_order():
    articles = [
        {"title": "Retailer opens new store", "event_type": "general_market_news", "risk_score": 3},
        {"title": "Blueberry recall expands", "event_type": "risk_event", "risk_score": 7},
        {"title": "Storm threatens Georgia deliveries", "event_type": "weather_disruption", "risk_score": 8},
    ]
    top = _rows()[_rows()["signal_area"] == "Weather Risk"]
    assert rank_articles_for_signals(articles, top)[0]["title"].startswith("Storm")


def test_fallback_brief_reports_confidence_by_source_and_state():
    brief = generate_fallback_brief(_rows(), "Dollar Tree", "US")
    assert "Confidence varies by source" in brief and "GNews Medium" in brief
    assert "unconfirmed until" in brief
    assert "GA Weather Risk" in brief


def test_tied_top_signals_are_a_bulleted_list():
    from market_intelligence.signals.retail_context import top_signal_labels

    labels, level = top_signal_labels(_rows())
    assert level == "High" and labels == ["Product Recalls", "Gasoline CPI", "GA Weather Risk"]  # highest score first
    brief = generate_fallback_brief(_rows(), "Dollar Tree", "US")
    assert "- Product Recalls\n- Gasoline CPI\n- GA Weather Risk" in brief


def test_previous_run_table_shows_readable_signal_names():
    import inspect
    from market_intelligence.ui.views import results

    assert "readable_feature_values(comparison_df" in inspect.getsource(results.render_previous_run_comparison)


def test_run_history_top_signal_matches_results_header():
    text, level = top_signals_summary(_rows())
    assert "GA Weather Risk" in text and level == "High"


def test_missing_exposure_fields_do_not_claim_dc_check():
    note = weather_exposure_note(pd.Series({"region": "GA"}))
    assert "not recorded" in note and "DC and route exposure has not been evaluated" in note


def test_blanket_high_confidence_is_rejected():
    from market_intelligence.llm.validation import validate_executive_brief

    base = (
        "EXECUTIVE SUMMARY\nWeather and recalls need attention this week.\n\nTOP INSIGHTS\n- Weather Risk — NOAA — High: severe alert.\n"
        "- Inflation — BLS — Medium: CPI pressure.\n- Product Recalls — openFDA — High: food safety.\n\nPLANNING RELEVANCE\n- Review staffing and routes.\n\n"
        "RECOMMENDED ACTIONS\n- Validate store exposure.\n- Review DC inventory.\n- Refresh before order cut-off.\n\nCONFIDENCE AND LIMITATIONS\n"
    )
    bad = base + "Overall confidence is High based on source credibility."
    good = base + "Confidence varies by source: NOAA High, news Medium. Operational impact is unconfirmed."
    assert not validate_executive_brief(bad)[0]
    assert validate_executive_brief(good)[0]


def test_cpi_rows_get_run_date_and_data_period():
    rows = _rows()
    rows["date"] = ["2026-08", "2026-08", "2026-10-08", "2026-10-08", "2026-10-08", "2026-10-08"]
    table = client_feature_table(rows, for_export=True, run_date="2026-10-08")
    assert (table["Run Date"] == "2026-10-08").all()
    assert list(table["Data Period"][:2]) == ["Aug 2026", "Aug 2026"]
    assert (table["Data Period"][2:] == "At run time").all()
