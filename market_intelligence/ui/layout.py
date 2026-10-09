from datetime import datetime
from html import escape
from market_intelligence.ui.state import set_current_retailer_label
import os
import streamlit as st

from market_intelligence.config.flags import show_admin_controls, signal_search_enabled
from market_intelligence.signals.search import build_default_news_keywords, build_default_trends_keywords, retailer_initials


def _last_run_status(timestamp) -> tuple[str, str]:
    """Header chip text: when the last run finished, or that none has happened yet."""
    try:
        parsed = datetime.strptime(str(timestamp), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return "No run yet", "idle"
    return f"Last run {parsed:%d %b, %H:%M} UTC", "live"


def render_header_and_navigation(ctx) -> None:
    nvidia_key = ctx.nvidia_key
    region = ctx.region
    retailer = ctx.retailer
    use_bls = ctx.use_bls
    use_fda = ctx.use_fda
    use_gnews = ctx.use_gnews
    use_weather = ctx.use_weather
    retailer_label = retailer.strip() or "General Retail Market"
    set_current_retailer_label(retailer_label)
    previous_keyword_retailer = st.session_state.get("keyword_template_retailer")
    store_states = [part.strip() for part in str(st.session_state.get("weather_area", "TX, GA")).split(",") if part.strip()]
    previous_news_template = build_default_news_keywords(previous_keyword_retailer or retailer_label, store_states)
    previous_trends_template = build_default_trends_keywords(previous_keyword_retailer or retailer_label)
    next_news_template = build_default_news_keywords(retailer_label, store_states)
    next_trends_template = build_default_trends_keywords(retailer_label)
    if "news_keywords_text" not in st.session_state or st.session_state.get("news_keywords_text") == previous_news_template:
        st.session_state["news_keywords_text"] = next_news_template
    if "trends_keywords_text" not in st.session_state or st.session_state.get("trends_keywords_text") == previous_trends_template:
        st.session_state["trends_keywords_text"] = next_trends_template
    st.session_state["keyword_template_retailer"] = retailer_label
    apify_source_active = False

    active_sources = sum([use_gnews, use_bls, use_fda, use_weather, apify_source_active])
    last_run = st.session_state.get("run") or {}
    status_text, status_class = _last_run_status(last_run.get("timestamp"))
    llm_mode = "Active" if nvidia_key.strip() else "Rule-based"
    st.markdown(
        "<div class='appbar'>"
        "<div class='appbar-title' title='Normalizes news, CPI, recalls, weather alerts and API health into "
        "signal levels, buyer actions and a demand-planning handoff.'>"
        "Retail Signal <span>Intelligence</span></div>"
        "<dl class='appbar-context'>"
        f"<div><dt>Retailer</dt><dd>{escape(retailer_label)}</dd></div>"
        f"<div><dt>Region</dt><dd>{escape(region)}</dd></div>"
        f"<div><dt>Sources</dt><dd>{active_sources} enabled</dd></div>"
        f"<div><dt>Agent</dt><dd>{llm_mode}</dd></div>"
        "</dl>"
        f"<div class='appbar-status {status_class}'><i></i>{escape(status_text)}</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    # Order follows the demo walkthrough: setup, results, operations, planning, audit, optional search.
    view_options = ["Configure", "Results", "Operational Impact Center", "Demand Planner", "Evidence Audit"]
    if signal_search_enabled():
        view_options.append("Signal Search")
    if show_admin_controls():
        view_options.append("Raw Data")
    if st.session_state.get("workbench_view") not in view_options:
        st.session_state["workbench_view"] = "Configure"

    nav_col, mode_col = st.columns([8.4, 1.6], vertical_alignment="center")
    with nav_col:
        view = st.segmented_control(
            "Workbench view",
            view_options,
            label_visibility="collapsed",
            key="workbench_view",
            width="content",
        )
    with mode_col:
        st.toggle("Admin view", key="admin_view", help="Show engineering detail (feature names, owners, KPIs, advanced settings).")
    ctx.admin_view = bool(st.session_state.get("admin_view", False))

    run_status_slot = st.empty()
    if "active_sources" in locals():
        ctx.active_sources = active_sources
    if "retailer_label" in locals():
        ctx.retailer_label = retailer_label
    if "run_status_slot" in locals():
        ctx.run_status_slot = run_status_slot
    if "view" in locals():
        ctx.view = view
