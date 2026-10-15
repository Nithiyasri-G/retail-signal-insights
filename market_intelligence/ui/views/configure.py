from html import escape
import streamlit as st

from market_intelligence.collectors.live_noaa import normalize_weather_areas
from market_intelligence.signals.labels import cpi_month_note, latest_cpi_month
from market_intelligence.ui.views.results import render_glossary, render_workflow_strip


def _section_title(text: str) -> None:
    st.markdown(f"<div class='section-title'>{escape(text)}</div>", unsafe_allow_html=True)


def _latest_cpi_month(run) -> str:
    """The BLS reporting month behind the last run's CPI rows, e.g. "Sep 2026" ("" if none)."""
    return latest_cpi_month((run or {}).get("feature_df"))


def render_configure(ctx) -> None:
    active_sources = ctx.active_sources
    nvidia_key = ctx.nvidia_key
    region = ctx.region
    retailer_label = ctx.retailer_label
    use_bls = ctx.use_bls
    use_fda = ctx.use_fda
    use_gnews = ctx.use_gnews
    use_weather = ctx.use_weather
    weather_areas = ", ".join(normalize_weather_areas(st.session_state.get("weather_area", "TX, GA")))
    # BLS publishes CPI monthly, about two weeks after the month ends, so a run in mid-October
    # carries September CPI. Say so where the sources are listed.
    cpi_month = _latest_cpi_month(st.session_state.get("run"))
    bls_signal = (
        "Headline and category inflation · US national, monthly · latest published month"
        + (f": {cpi_month}" if cpi_month else "")
        + " (released about 2 weeks after month end)"
    )
    source_specs = [
        {
            "name": "GNews/RSS",
            "status": "Enabled" if use_gnews else "Disabled",
            "signal": "Retailer and retail-market news",
            "feature": "retail_news_event_score",
            "owner": "Category Manager",
            "output": "Market Event Risk",
        },
        {
            "name": "BLS CPI",
            "status": "Enabled" if use_bls else "Disabled",
            "signal": bls_signal,
            "feature": "cpi_pressure_features",
            "owner": "Demand Planning",
            "output": "Value Basket Pressure",
        },
        {
            "name": "openFDA",
            "status": "Enabled" if use_fda else "Disabled",
            "signal": "Food recall enforcement records",
            "feature": "recall_exposure_score",
            "owner": "Compliance + Buyer",
            "output": "Safety and Compliance Risk",
        },
        {
            "name": "NOAA Weather",
            "status": "Enabled" if use_weather else "Disabled",
            "signal": f"Weather alerts for {weather_areas} (set in NOAA weather area)",
            "feature": "state_weather_disruption_score",
            "owner": "Supply Chain",
            "output": "Route And Store Risk",
        },
    ]
    admin = bool(getattr(ctx, "admin_view", False))
    keywords_set = bool(str(st.session_state.get("news_keywords_text", "")).strip())
    llm_ready = bool(nvidia_key.strip())
    checks = [
        ("Retailer", bool(ctx.retailer.strip()), retailer_label if ctx.retailer.strip() else "Not set"),
        ("Region", bool(region.strip()), region if region.strip() else "Not set"),
        ("Public sources", active_sources > 0, f"{active_sources} of 4 enabled" if active_sources else "None enabled"),
        ("News keywords", keywords_set, "Configured" if keywords_set else "Empty"),
    ]
    if use_weather:
        checks.append(("Weather coverage", True, f"{weather_areas} only"))
    blocking = [label for label, ok, _ in checks if not ok]
    summary = "Ready to run" if not blocking else f"Needs attention: {', '.join(blocking)}"
    summary_state = "ok" if not blocking else "warn"
    brief_note = "Executive brief: written by agent" if llm_ready else "Executive brief: rule-based"
    check_rows = "".join(
        f"<div class='check-row'><span class='check-icon {'ok' if ok else 'warn'}'>{'&#10003;' if ok else '!'}</span>"
        f"<span class='check-label'>{escape(label)}</span><span class='check-value'>{escape(value)}</span></div>"
        for label, ok, value in checks
    )
    show_rows = admin or bool(blocking)
    # Changing the news market only changes GNews; CPI, FDA and NOAA stay US sources. Say so
    # on the main view, not only in Advanced settings.
    news_country = str(getattr(ctx, "country", "") or st.session_state.get("country", "") or "US").strip().upper()
    market_note = (
        f"<div class='panel-foot'>News market: {escape(news_country)}. Inflation CPI (BLS), FDA recalls and NOAA weather "
        "remain US sources and are not changed by this setting.</div>"
        if news_country and news_country != "US"
        else ""
    )
    # One full-width card: the readiness verdict is a status line under the Data Sources
    # title. The pipeline status card above already shows the run itself, and the left
    # panel shows retailer, region and sources, so a separate readiness card only repeated them.
    with st.container(key="sources_card"):
        head = "<th>Source</th><th>Status</th><th>What it tells you</th>"
        if admin:
            head += "<th>Forecast feature</th><th>Owner</th><th>KPI</th>"
        body = []
        for spec in source_specs:
            on = spec["status"] == "Enabled"
            row = (
                f"<td>{escape(spec['name'])}</td>"
                f"<td><span class='status-dot {'on' if on else 'off'}'></span>{escape(spec['status'])}</td>"
                f"<td>{escape(spec['signal'])}</td>"
            )
            if admin:
                row += (
                    f"<td><code>{escape(spec['feature'])}</code></td>"
                    f"<td>{escape(spec['owner'])}</td>"
                    f"<td>{escape(spec['output'])}</td>"
                )
            body.append(f"<tr>{row}</tr>")
        st.markdown(
            "<div class='panel'>"
            "<div class='panel-title'>Data sources</div>"
            f"<div class='panel-status {summary_state}'>{escape(summary)} &middot; {escape(brief_note)}</div>"
            f"{market_note}"
            f"{check_rows if show_rows else ''}"
            f"<table class='data-table'><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
            "</div>",
            unsafe_allow_html=True,
        )
        if blocking:
            st.caption("Resolve the items above, then use Run intelligence in the left panel.")
    st.markdown(
        "<div class='notice-bar'>Based on public external signals only. Internal sales and inventory data "
        "are not connected. Raw payloads are in Raw Data.</div>",
        unsafe_allow_html=True,
    )

    with st.expander("Advanced settings", expanded=admin):
        setup_tab, collector_tab, governance_tab = st.tabs(["Scope", "Collector Tuning", "Governance"])
        with setup_tab:
            _section_title("News keywords")
            st.caption(
                "One search term per line: the retailer, competitors, key categories and risk terms "
                "(recall, tariff, closure, promotion). Changing them changes the evidence collected; "
                "Evidence Audit records the exact scope of each run."
            )
            st.text_area("GNews keywords", key="news_keywords_text", height=140, label_visibility="collapsed")
        with collector_tab:
            st.caption("Controls run time and evidence volume. Defaults are conservative to keep each run explainable and predictable in cost.")

            c1, c2 = st.columns(2)
            with c1:
                _section_title("News")
                st.selectbox("GNews period", ["1d", "7d", "30d", "3m"], key="gnews_period")
                st.slider("Max news results", min_value=5, max_value=60, step=1, key="max_news")
                _section_title("Inflation (BLS CPI)")
                st.caption(cpi_month_note(cpi_month))
            with c2:
                _section_title("Recalls and weather")
                st.text_input(
                    "FDA recall search",
                    key="fda_query",
                    help=(
                        "openFDA query. It only limits which ongoing recall records are collected; it does "
                        "not confirm that any product is carried internally. Each returned recall is then "
                        "matched separately against the internal demonstration catalog. The default covers "
                        "every food category the catalog covers (snacks, candy, beverages, water, soup, "
                        "bread, cookies, cereal, peanut butter, granola bar, trail mix, infant/baby "
                        "formula). If this field is left empty, the default search is used."
                    ),
                )
                st.slider(
                    "FDA recall limit",
                    min_value=1,
                    max_value=25,
                    key="fda_limit",
                    help="How many recent openFDA records to pull per run. Higher = more chance of a live match against the internal catalog, at the cost of a slower run.",
                )
                st.text_input(
                    "NOAA weather area",
                    key="weather_area",
                    help=(
                        "Two-letter US state code(s). NOAA is queried for these states only; the "
                        "Region field (e.g. 'US') is a label and does not control NOAA coverage. A live "
                        "incident can only appear for a state listed here, so list every state with a "
                        "store or DC, comma-separated (e.g. 'TX, GA'). GNews keywords do not affect NOAA."
                    ),
                )
                st.slider("NOAA alert limit", min_value=1, max_value=25, key="weather_limit")
        with governance_tab:
            st.caption("Every figure is traceable: source status is reported separately from brief status, a locally generated brief is labelled as such, and raw collector payloads stay inspectable in Raw Data.")

            g1, g2 = st.columns([1, 1])
            with g1:
                _section_title("How a run works")
                render_workflow_strip()
            with g2:
                _section_title("Glossary")
                render_glossary()
