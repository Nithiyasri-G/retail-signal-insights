import streamlit as st

from market_intelligence.config.settings import APIFY_ALLOWED_TIME_RANGES, APIFY_HARD_KEYWORD_LIMIT, APIFY_SAFE_TIME_RANGE
from market_intelligence.persistence.operational_store import init_operational_impact_db
from market_intelligence.persistence.run_history import init_run_history_db


def init_app_state(ctx) -> None:
    init_run_history_db()
    init_operational_impact_db()

    st.session_state.setdefault("signal_search_keywords", "")
    st.session_state.setdefault("signal_search_time_window", "Last 1 day")
    st.session_state.setdefault("signal_search_geo", "United States - National")
    st.session_state.setdefault("gnews_period", "7d")
    st.session_state.setdefault("max_news", 48)
    st.session_state.setdefault("fda_query", "product_description:(snacks OR candy OR beverages OR water OR soup OR bread OR cookies OR cereal OR \"peanut butter\" OR \"granola bar\" OR \"trail mix\" OR \"infant formula\" OR \"baby formula\")")
    st.session_state.setdefault("fda_limit", 20)
    st.session_state.setdefault("weather_area", "TX, GA")
    st.session_state.setdefault("weather_limit", 5)
    st.session_state.setdefault("apify_geo", "US")
    st.session_state.setdefault("apify_time_range", APIFY_SAFE_TIME_RANGE)
    st.session_state.setdefault("apify_max_keywords", APIFY_HARD_KEYWORD_LIMIT)
    st.session_state.setdefault("apify_run_mode", "Skip Apify")
    st.session_state.setdefault("apify_live_confirm", False)
    st.session_state.setdefault("workbench_view", "Configure")
    if st.session_state.get("apify_time_range") not in APIFY_ALLOWED_TIME_RANGES:
        st.session_state["apify_time_range"] = APIFY_SAFE_TIME_RANGE
    if st.session_state.pop("force_results_view", False):
        st.session_state["workbench_view"] = "Results"
    if st.session_state.pop("reset_apify_live_confirm", False):
        st.session_state["apify_run_mode"] = "Skip Apify"
        st.session_state["apify_live_confirm"] = False
