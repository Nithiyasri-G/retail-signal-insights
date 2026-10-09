"""Streamlit wrapper around the Run Intelligence use case: builds the request from the sidebar and
session state, renders progress, and hands the finished run to session state."""
from typing import Any, Dict, Optional

import streamlit as st

from market_intelligence.application.run_intelligence import RunRequest, run_intelligence
from market_intelligence.collectors.live_noaa import normalize_weather_areas
from market_intelligence.config.settings import (
    APIFY_ALLOWED_TIME_RANGES,
    APIFY_HARD_KEYWORD_LIMIT,
    APIFY_SAFE_TIME_RANGE,
    BLS_CPI_SERIES,
)
from market_intelligence.signals.search import build_default_news_keywords, build_default_trends_keywords
from market_intelligence.signals.signal_log import parse_lines
from market_intelligence.ui.components import render_run_monitor, render_sidebar_status

DEFAULT_FDA_QUERY = (
    "product_description:(snacks OR candy OR beverages OR water OR soup OR bread OR cookies OR cereal OR "
    "\"peanut butter\" OR \"granola bar\" OR \"trail mix\" OR \"infant formula\" OR \"baby formula\")"
)


class _StreamlitRunObserver:
    """Renders run progress into the page and publishes the finished run to session state."""

    def __init__(self, run_status_slot: Any, sidebar_status_slot: Any) -> None:
        self.run_status_slot = run_status_slot
        self.sidebar_status_slot = sidebar_status_slot

    def progress(
        self,
        title: str,
        collector_states: Dict[str, Dict[str, str]],
        pct: int,
        sidebar_message: Optional[str] = None,
        sidebar_level: str = "info",
    ) -> None:
        render_run_monitor(self.run_status_slot, title, collector_states, pct)
        if sidebar_message is not None:
            render_sidebar_status(self.sidebar_status_slot, sidebar_message, sidebar_level)

    def brief_ready(self) -> None:
        if st.session_state.get("run"):
            st.session_state["previous_run"] = st.session_state["run"]

    def run_ready(self, completed_run: Dict[str, Any]) -> None:
        st.session_state["run"] = completed_run


def build_run_request(ctx) -> RunRequest:
    news_keywords = parse_lines(
        st.session_state.get("news_keywords_text", build_default_news_keywords(ctx.retailer_label))
    )
    trends_keywords = parse_lines(
        st.session_state.get("trends_keywords_text", build_default_trends_keywords(ctx.retailer_label))
    )
    apify_token_value = ctx.apify_token.strip()
    apify_run_mode = st.session_state.get("apify_run_mode", "Skip Apify")
    apify_time_range = st.session_state.get("apify_time_range", APIFY_SAFE_TIME_RANGE)
    if apify_time_range not in APIFY_ALLOWED_TIME_RANGES or not apify_time_range:
        apify_time_range = APIFY_SAFE_TIME_RANGE
    apify_live_confirmed = False  # Only Signal Search may trigger a paid actor call.
    apify_requested = bool(ctx.use_apify or apify_live_confirmed)
    weather_areas = normalize_weather_areas(st.session_state.get("weather_area", "TX"))
    run_config = {
        "retailer": ctx.retailer_label,
        "region": ctx.region,
        "country": ctx.country,
        "language": ctx.language,
        "enabled_sources": {
            "gnews": ctx.use_gnews, "bls": ctx.use_bls, "fda": ctx.use_fda,
            "weather": ctx.use_weather, "apify": apify_requested,
        },
        "use_gnews": ctx.use_gnews,
        "use_bls": ctx.use_bls,
        "use_fda": ctx.use_fda,
        "use_weather": ctx.use_weather,
        "use_apify": apify_requested,
        "news_keywords": news_keywords,
        "trends_keywords": trends_keywords[:APIFY_HARD_KEYWORD_LIMIT],
        "gnews_period": st.session_state.get("gnews_period", "7d"),
        "max_news": int(st.session_state.get("max_news", 24)),
        "fda_query": st.session_state.get("fda_query", DEFAULT_FDA_QUERY),
        "fda_limit": int(st.session_state.get("fda_limit", 20)),
        "weather_area": ", ".join(weather_areas),
        "weather_limit": int(st.session_state.get("weather_limit", 5)),
        "bls_series": BLS_CPI_SERIES,
        "apify_token_present": bool(apify_token_value),
        "apify_run_mode": apify_run_mode,
        "apify_geo": st.session_state.get("apify_geo", "US"),
        "apify_time_range": apify_time_range,
        "apify_max_keywords": int(st.session_state.get("apify_max_keywords", APIFY_HARD_KEYWORD_LIMIT)),
        "apify_live_confirm": apify_live_confirmed,
    }
    return RunRequest(
        retailer_label=ctx.retailer_label,
        region=ctx.region,
        country=ctx.country,
        language=ctx.language,
        use_gnews=ctx.use_gnews,
        use_bls=ctx.use_bls,
        use_fda=ctx.use_fda,
        use_weather=ctx.use_weather,
        bls_key=ctx.bls_key,
        nvidia_key=ctx.nvidia_key,
        nvidia_model=ctx.nvidia_model,
        news_keywords=news_keywords,
        trends_keywords=trends_keywords,
        gnews_period=st.session_state.get("gnews_period", "7d"),
        max_news=int(st.session_state.get("max_news", 24)),
        fda_query=st.session_state.get("fda_query", DEFAULT_FDA_QUERY),
        fda_limit=int(st.session_state.get("fda_limit", 20)),
        weather_areas=weather_areas,
        weather_limit=int(st.session_state.get("weather_limit", 5)),
        apify_requested=apify_requested,
        apify_time_range=apify_time_range,
        apify_geo=st.session_state.get("apify_geo", "US"),
        run_config=run_config,
    )


def run_intelligence_pipeline(ctx) -> None:
    request = build_run_request(ctx)
    observer = _StreamlitRunObserver(ctx.run_status_slot, ctx.sidebar_status_slot)
    outcome = run_intelligence(request, observer)
    render_sidebar_status(ctx.sidebar_status_slot, "Run complete. Opening Results...", "success")
    st.session_state["force_results_view"] = True
    st.session_state["last_collector_states"] = outcome.collector_states
    st.rerun()
