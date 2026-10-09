import json
import pandas as pd
import sqlite3
import streamlit as st

from market_intelligence.collectors.live_apify import collect_apify_trends
from market_intelligence.config.settings import APIFY_HARD_KEYWORD_LIMIT, RUN_HISTORY_DB
from market_intelligence.config.sources import SIGNAL_SEARCH_TIME_WINDOWS, US_SIGNAL_GEOS
from market_intelligence.persistence.run_history import load_run_history, save_run_history
from market_intelligence.signals.search import parse_signal_search_keywords, related_search_summary, search_interest_summary
from market_intelligence.util.clock import utc_now
from market_intelligence.util.tables import drop_empty_columns


def render_signal_search(ctx) -> None:
    apify_token = ctx.apify_token
    language = ctx.language
    retailer_label = ctx.retailer_label
    st.markdown('<div class="small-header">New Signal Search</div>', unsafe_allow_html=True)
    st.caption("Google Trends values are relative indexes, not absolute search volumes.")
    st.markdown(
        "<div class='config-tab-note'>"
        "Search Google Trends for a retail product or topic. Enter up to two terms, "
        "choose a geography and time window, and see where those terms appear in the regional results. "
        "Searches are saved separately from the intelligence assessment."
        "</div>",
        unsafe_allow_html=True,
    )

    search_left, search_mid, search_right = st.columns([1.5, 1, 1])
    with search_left:
        st.text_area(
            "Signal keywords",
            key="signal_search_keywords",
            height=96,
            placeholder="Example: winter storm\nbatteries",
            help=f"Enter up to {APIFY_HARD_KEYWORD_LIMIT} keywords, separated by a new line or comma.",
        )
    with search_mid:
        st.selectbox(
            "Time window",
            list(SIGNAL_SEARCH_TIME_WINDOWS.keys()),
            key="signal_search_time_window",
        )
    with search_right:
        st.selectbox(
            "U.S. geography",
            list(US_SIGNAL_GEOS.keys()),
            key="signal_search_geo",
        )

    st.caption(
        f"Source: Apify Google Trends · Maximum {APIFY_HARD_KEYWORD_LIMIT} keywords per search · "
        "Search Trends makes one live Apify call."
    )
    generate_signal_button = st.button("Search Trends", type="primary")

    if generate_signal_button:
        search_keywords = parse_signal_search_keywords(st.session_state.get("signal_search_keywords", ""))
        search_window_label = st.session_state.get("signal_search_time_window", "Last 1 day")
        search_time_range = SIGNAL_SEARCH_TIME_WINDOWS.get(search_window_label, "now 1-d")
        search_geo_label = st.session_state.get("signal_search_geo", "United States - National")
        search_geo = US_SIGNAL_GEOS.get(search_geo_label, "US")

        if not apify_token.strip():
            st.error("Please provide the Apify token under Credentials before generating a live signal.")
        elif not search_keywords:
            st.error("Please enter at least one signal keyword.")
        elif len(search_keywords) > APIFY_HARD_KEYWORD_LIMIT:
            st.error(f"Please enter no more than {APIFY_HARD_KEYWORD_LIMIT} keywords for one search.")
        else:
            with st.spinner("Searching Google Trends..."):
                apify_result = collect_apify_trends(
                    apify_token.strip(),
                    search_keywords,
                    search_geo,
                    search_time_range,
                    retailer_label,
                    len(search_keywords),
                    include_scored_signal=False,
                )

                if apify_result.get("status") != "success":
                    st.error(apify_result.get("error") or "Apify did not return a usable signal for this search.")
                else:
                    search_feature_df = pd.DataFrame()

                    search_config = {
                        "retailer": retailer_label,
                        "region": search_geo_label,
                        "country": "US",
                        "language": language,
                        "enabled_sources": {"gnews": False, "bls": False, "fda": False, "weather": False, "apify": True},
                        "use_gnews": False,
                        "use_bls": False,
                        "use_fda": False,
                        "use_weather": False,
                        "use_apify": True,
                        "news_keywords": [],
                        "trends_keywords": search_keywords,
                        "apify_token_present": True,
                        "apify_run_mode": "Generate Signal",
                        "apify_geo": search_geo,
                        "apify_time_range": search_time_range,
                        "apify_max_keywords": len(search_keywords),
                        "apify_live_confirm": True,
                        "signal_search": True,
                    }
                    search_results = {"apify": apify_result}
                    # A keyword lookup is evidence exploration, not a scored AI brief.
                    search_brief, search_brief_source, search_llm_audit = "", "not_requested", {}
                    search_run = {
                        "timestamp": utc_now(),
                        "run_config": search_config,
                        "results": search_results,
                        "feature_df": search_feature_df,
                        "articles": [],
                        "brief": search_brief,
                        "brief_source": search_brief_source,
                        "llm_audit": search_llm_audit,
                    }
                    st.session_state["saved_search_signal"] = search_run
                    save_run_history(
                        search_run,
                        run_type="Signal Search",
                        source="Apify Google Trends",
                        keywords=", ".join(search_keywords),
                        time_window=search_window_label,
                        geography=search_geo_label,
                    )
                    st.success("Search results saved. The main intelligence run is unchanged.")

    saved_search = st.session_state.get("saved_search_signal")
    if saved_search:
        config = saved_search.get("run_config", {})
        st.markdown('<div class="small-header">Search findings</div>', unsafe_allow_html=True)
        st.caption(
            f"Search terms: {', '.join(config.get('trends_keywords', []))} · "
            f"Geography: {config.get('region', 'US')} · "
            f"Collected: {saved_search['timestamp']} · Source: Apify Google Trends"
        )
        findings = search_interest_summary(saved_search.get("results", {}).get("apify", {}).get("items", []))
        if findings.empty:
            st.info("No regional search-interest findings were available for these terms and time window.")
        else:
            st.dataframe(findings, hide_index=True, width="stretch")
            st.caption("These are regions returned for the selected terms, not measured sales or a forecast.")
        related = related_search_summary(saved_search.get("results", {}).get("apify", {}).get("raw", []))
        if not related.empty:
            st.markdown('<div class="small-header">Related Searches</div>', unsafe_allow_html=True)
            st.dataframe(related, hide_index=True, width="stretch")
            st.caption("Rising and related terms are shown as returned by Google Trends; they are not a sales forecast.")
    st.markdown('<div class="small-header">Recent Searches</div>', unsafe_allow_html=True)
    history_df = load_run_history(50)
    history_df = history_df[history_df["Run Type"] == "Signal Search"].head(10)
    if history_df.empty:
        st.info("No saved searches yet.")
    else:
        history_cols = drop_empty_columns(history_df, [c for c in history_df.columns if c not in {"Top Signal", "Level"}])
        st.dataframe(history_df[history_cols], width="stretch", hide_index=True)
        selected_history = st.selectbox("Saved run to restore", history_df["Run Time"].astype(str).tolist(), key="restore_run_time")
        if st.button("Restore saved run"):
            with sqlite3.connect(RUN_HISTORY_DB) as connection:
                try:
                    saved_row = connection.execute("SELECT payload_json FROM run_evidence WHERE timestamp=?", (selected_history,)).fetchone()
                except sqlite3.OperationalError:
                    saved_row = None
            if saved_row:
                restored = json.loads(saved_row[0])
                restored["feature_df"] = pd.DataFrame(restored.get("feature_df", []))
                for incident in restored.get("operational_incidents_snapshot", []):
                    for key in ("scenario_df", "staffing_df", "employee_detail_df", "traffic_df"):
                        scenario = incident.get("Scenario", {})
                        if isinstance(scenario.get(key), list):
                            scenario[key] = pd.DataFrame(scenario[key])
                if restored.get("run_config", {}).get("signal_search"):
                    st.session_state["saved_search_signal"] = restored
                    st.success("Saved search restored. No Apify call was made.")
                    st.rerun()
                else:
                    st.warning("Select a Signal Search entry to restore a keyword lookup.")
            else:
                st.warning("This older run contains summary history only. Export a new complete run.")
