from html import escape
from market_intelligence.ui.state import current_retailer_label
from typing import Any
from typing import Dict
from typing import Optional
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from market_intelligence.llm.audit import display_fallback_reason
from market_intelligence.signals.labels import readable_feature_values, readable_region_scope, readable_signal_name, with_client_labels
from market_intelligence.signals.retail_context import signal_display_label, signal_short_label, top_signal_labels
from market_intelligence.signals.risk import risk_band
from market_intelligence.signals.scoring import build_internal_validation_plan, build_previous_run_comparison, build_recommended_actions, build_trust_metrics, scoring_formula_for_row
from market_intelligence.ui.components import render_metric_card, style_level_dataframe
from market_intelligence.util.tables import format_timestamp


def render_trust_panel(run: Dict[str, Any], feature_df: pd.DataFrame) -> None:
    cards = []
    for metric in build_trust_metrics(run, feature_df):
        cards.append(
            f"<div class='trust-card {escape(metric.get('state', 'good'))}'>"
            f"<div class='trust-label'>{escape(metric['label'])}</div>"
            f"<div class='trust-value'>{escape(metric['value'])}</div>"
            f"<div class='trust-note'>{escape(metric['note'])}</div>"
            "</div>"
        )
    st.markdown("<div class='trust-grid'>" + "".join(cards) + "</div>", unsafe_allow_html=True)


def render_previous_run_comparison(feature_df: pd.DataFrame, previous_run: Optional[Dict[str, Any]]) -> None:
    comparison_df = build_previous_run_comparison(feature_df, previous_run)
    if comparison_df.empty:
        st.caption("Comparison with the previous run appears here once there are two runs in this session.")
        return
    inc = int((comparison_df["movement"] == "Increased").sum())
    dec = int((comparison_df["movement"] == "Decreased").sum())
    stable = int((comparison_df["movement"] == "Stable").sum())
    cols = st.columns(4)
    with cols[0]:
        render_metric_card("Compared Rows", str(len(comparison_df)), "Matched by source, signal, and region.")
    with cols[1]:
        render_metric_card("Increased", str(inc), "Signals that moved to a higher level.")
    with cols[2]:
        render_metric_card("Decreased", str(dec), "Signals that moved to a lower level.")
    with cols[3]:
        render_metric_card("Stable", str(stable), "Signals that remained at the same level.")
    display_comparison = readable_feature_values(comparison_df.copy())
    display_comparison["Previous Level"] = display_comparison["previous_score"].apply(lambda x: "New" if pd.isna(x) else risk_band(float(x)))
    display_comparison["Current Level"] = display_comparison["current_score"].apply(lambda x: risk_band(float(x or 0)))
    visible_cols = [col for col in ["source", "signal_area", "signal_name", "region", "retail_category", "planning_owner", "Previous Level", "Current Level", "movement"] if col in display_comparison.columns]
    st.dataframe(style_level_dataframe(with_client_labels(display_comparison[visible_cols])), width="stretch", hide_index=True)


def render_internal_validation_agent(feature_df: pd.DataFrame) -> None:
    plan_df = build_internal_validation_plan(feature_df)
    if plan_df.empty:
        st.info("Internal validation guidance will appear after feature rows are generated.")
        return
    st.dataframe(with_client_labels(plan_df), width="stretch", hide_index=True)


def render_enterprise_context(retailer_name: str) -> None:
    st.markdown(
        "<div class='enterprise-band'>"
        f"<div class='enterprise-band-title'>{escape(retailer_name)} External Signal Control Layer</div>"
        "<div class='enterprise-band-copy'>"
        "This view translates public signals into category, owner, forecast-feature, and action language for buyers, category managers, demand planners, compliance, and supply-chain teams. "
        "It does not claim internal category performance until POS, inventory, product hierarchy, promotion, vendor, and store/DC data are connected."
        "</div></div>",
        unsafe_allow_html=True,
    )


def _level_class(band: str) -> str:
    return f"lv-{band.lower()}" if band.lower() in {"high", "medium", "low"} else "lv-none"


def render_results_command_header(run: Dict[str, Any], feature_df: pd.DataFrame) -> None:
    """Headline strip: the four facts a reader needs before anything else."""
    run_config = run.get("run_config", {})
    retailer_name = str(run_config.get("retailer", current_retailer_label()))
    # Audited count: sources that actually made a live request (same figure as the trust
    # panel), not merely the number of source names present in the feature rows.
    live_source_count = next(
        (m["value"] for m in build_trust_metrics(run, feature_df) if m.get("label") == "Live Sources"), "0"
    )
    overall = "No signal"
    top_labels: list = []
    top_band = ""
    if not feature_df.empty and "risk_score" in feature_df.columns:
        overall = risk_band(float(feature_df["risk_score"].mean()))
        top_labels, top_band = top_signal_labels(feature_df)
    # Tied top signals are a bulleted list (one per line), not a comma-joined sentence.
    if len(top_labels) > 1:
        top_html = "<ul class='headline-list'>" + "".join(f"<li>{escape(label)}</li>" for label in top_labels) + "</ul>"
    else:
        top_html = escape(top_labels[0] if top_labels else "No signal")
    top_label_html = ("Top signals" if len(top_labels) > 1 else "Top signal") + (
        f" <span class='headline-sub {_level_class(top_band)}'>{escape(top_band)}</span>" if top_band else ""
    )
    source_total = int(live_source_count) if str(live_source_count).isdigit() else 0
    cells = [
        ("Overall signal level", escape(overall), _level_class(overall)),
        (top_label_html, top_html, ""),
        ("Run time", escape(format_timestamp(run.get("timestamp", "")) or "Not recorded"), ""),
        ("Live sources", escape(f"{live_source_count} source" + ("" if source_total == 1 else "s")), ""),
    ]
    cells_html = "".join(
        f"<div class='headline-cell'><div class='headline-label'>{label}</div>"
        f"<div class='headline-value {cls}'>{value}</div></div>"
        for label, value, cls in cells
    )
    st.markdown(
        f"<div class='section-title' style='font-size:18px;margin-top:8px;'>{escape(retailer_name)} external signal readout</div>"
        f"<div class='headline-strip'>{cells_html}</div>",
        unsafe_allow_html=True,
    )


def render_data_confidence(run: Dict[str, Any], feature_df: pd.DataFrame) -> None:
    """One line of data provenance plus a plain banner when internal data is not real."""
    metrics = build_trust_metrics(run, feature_df)
    parts = [f"{m['label']}: <strong>{escape(str(m['value']))}</strong>" for m in metrics]
    llm_audit = run.get("llm_audit", {})
    reason = llm_audit.get("fallback_reason")
    note = display_fallback_reason(reason).strip() if reason else ""
    st.markdown(
        "<div class='confidence-line'>" + " &middot; ".join(parts) + "</div>"
        + (f"<div class='confidence-note'>{escape(note)}</div>" if note else ""),
        unsafe_allow_html=True,
    )
    for m in metrics:
        if str(m.get("label", "")).strip().lower() == "internal operations" and m.get("state") == "warn":
            st.markdown(
                f"<div class='notice-bar'><strong>Internal operations data: {escape(str(m['value']))}.</strong> "
                f"{escape(str(m['note']))}</div>",
                unsafe_allow_html=True,
            )

def render_decision_summary(feature_df: pd.DataFrame, results: Dict[str, Dict[str, Any]]) -> None:
    cards = []
    for action in build_recommended_actions(feature_df, results):
        why, _, next_step = str(action["body"]).partition("Next:")
        next_html = (
            f"<div class='decision-label'>Next step</div><div class='decision-body'>{escape(next_step.strip())}</div>"
            if next_step.strip()
            else ""
        )
        cards.append(
            "<div class='decision-card'>"
            f"<div class='decision-owner'>{escape(action['label'])}</div>"
            f"<div class='decision-title'>{escape(action['title'])}</div>"
            f"<div class='decision-label'>Why</div><div class='decision-body'>{escape(why.strip())}</div>"
            f"{next_html}"
            "</div>"
        )
    if not cards:
        cards.append(
            "<div class='decision-card'><div class='decision-owner'>Setup</div><div class='decision-title'>Run signal sources</div><div class='decision-body'>No decision cards are available until normalized external signals are generated.</div></div>"
        )
    st.markdown("<div class='decision-grid'>" + "".join(cards[:4]) + "</div>", unsafe_allow_html=True)


def render_explainability_ladder() -> None:
    steps = [
        ("01", "Collect", "Live public APIs return raw evidence; skipped sources are labeled."),
        ("02", "Normalize", "Records become normalized external signals with source, region, and level fields."),
        ("03", "Map", "Rows are mapped to retail category, owner, KPI, and forecast feature."),
        ("04", "Classify", "Each source uses the existing rule to classify the signal as High, Medium, or Low."),
        ("05", "Brief", "The summary covers only the rows and article context shown here."),
    ]
    html = "".join(
        "<div class='explain-step'>"
        f"<div class='explain-step-num'>{escape(num)}</div>"
        f"<div class='explain-step-title'>{escape(title)}</div>"
        f"<div class='explain-step-copy'>{escape(copy)}</div>"
        "</div>"
        for num, title, copy in steps
    )
    st.markdown("<div class='explain-ladder'>" + html + "</div>", unsafe_allow_html=True)


def render_retail_impact_matrix(feature_df: pd.DataFrame, admin: bool = False) -> None:
    if feature_df.empty:
        st.info("No retail impact matrix is available until feature rows are generated.")
        return
    preferred_cols = [
        "retail_category",
        "region",
        "enterprise_kpi",
        "risk_score",
        "action_priority",
        "planning_owner",
    ]
    if admin:
        preferred_cols += ["demand_direction", "forecast_feature", "impact_hypothesis", "internal_data_needed", "source"]
    visible_cols = [col for col in preferred_cols if col in feature_df.columns]
    sort_cols = [col for col in ["action_priority", "risk_score"] if col in feature_df.columns]
    ascending = [True if col == "action_priority" else False for col in sort_cols]
    matrix_source = feature_df.sort_values(sort_cols, ascending=ascending) if sort_cols else feature_df
    matrix = readable_feature_values(matrix_source[visible_cols].copy())
    if "risk_score" in matrix.columns:
        matrix["risk_score"] = matrix["risk_score"].apply(lambda x: risk_band(float(x or 0)))
        matrix = matrix.rename(columns={"risk_score": "Risk Level"})
    labelled = with_client_labels(matrix)
    widths = {"Retail Category": "medium", "Region": "small", "Enterprise KPI": "medium", "Risk Level": "small", "Action Priority": "small", "Planning Owner": "medium"}
    column_config = None if admin else {
        name: st.column_config.TextColumn(width=width) for name, width in widths.items() if name in labelled.columns
    }
    st.dataframe(style_level_dataframe(labelled), width="stretch", hide_index=True, column_config=column_config)


def render_scenario_simulator(feature_df: pd.DataFrame) -> None:
    scenarios = {
        "Inflation rises again": {
            "category": "Total value basket, food, household essentials",
            "owner": "Merchandising Strategy + Demand Planning",
            "feature": "headline_cpi_value_pressure",
            "action": "Watch trade-down behavior, validate basket mix, and review value-sensitive replenishment.",
        },
        "FDA recall affects consumables": {
            "category": "Snacks, candy, beverages, consumables",
            "owner": "Compliance + Category Buyer",
            "feature": "recall_exposure_score",
            "action": "Match recall UPCs against the internal SKU master. If a match is confirmed, isolate the affected inventory and monitor substitute-item demand.",
        },
        "Severe weather hits selected state": {
            "category": "Emergency demand and replenishment-sensitive categories",
            "owner": "Supply Chain + Demand Planning",
            "feature": "state_weather_disruption_score",
            "action": "Check store/DC exposure, route risk, and short-horizon emergency-demand uplift.",
        },
        "Competitor promotion pressure rises": {
            "category": "Overlapping value categories and seasonal assortment",
            "owner": "Buyer + Category Manager",
            "feature": "competitor_promotion_pressure_score",
            "action": "Compare overlapping items, review promotional calendar, and watch category conversion.",
        },
    }
    selected = st.selectbox("Scenario", list(scenarios.keys()), label_visibility="collapsed")
    scenario = scenarios[selected]
    evidence_note = "Illustrative scenario \u2014 not detected in this run. No current run evidence matched this scenario directly."
    if not feature_df.empty and "forecast_feature" in feature_df.columns:
        matching = feature_df[feature_df["forecast_feature"].astype(str).str.contains(scenario["feature"].split("_")[0], case=False, na=False)]
        if not matching.empty:
            # List every matching row with its state, so a High GA row is never read as
            # applying to a Medium TX row (weather signals are state-specific).
            matching = matching.sort_values("risk_score", ascending=False, kind="mergesort")
            parts = [
                f"{signal_short_label(row)} ({row.get('source')}) is {risk_band(float(row.get('risk_score', 0) or 0))}"
                for _, row in matching.head(3).iterrows()
            ]
            evidence_note = "Nearest current signal(s): " + "; ".join(parts) + "."
    st.markdown(
        "<div class='enterprise-band'>"
        f"<div class='enterprise-band-title'>{escape(selected)}</div>"
        f"<div class='enterprise-band-copy'><strong>Likely retail category:</strong> {escape(scenario['category'])}<br>"
        f"<strong>Owner:</strong> {escape(scenario['owner'])}<br>"
        f"<strong>Forecast feature:</strong> {escape(scenario['feature'])}<br>"
        f"<strong>Action:</strong> {escape(scenario['action'])}<br>"
        f"<strong>Run evidence:</strong> {escape(evidence_note)}</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def render_glossary() -> None:
    terms = [
        ("Signal", "An external event or measurement that may explain demand, price pressure, or operational risk."),
        ("Risk Level", "A directional level. Higher means the signal deserves more attention, not that demand is guaranteed to move."),
        ("Forecast Feature", "A structured column that can later be joined to internal sales, store, category, promotion, and inventory data."),
        ("Region Scope", "Whether the signal is national, state-level, trend geography, or selected market context."),
        ("Rule-based Summary", "A deterministic summary written from the same signal rows when the agent is unavailable or not configured."),
        ("Mock Data", "Store, DC, inventory and staffing records generated for this proof of concept. External signals are live; internal operations data is synthetic until real systems are connected."),
    ]
    cards = []
    for term, definition in terms:
        cards.append(
            "<div class='glossary-card'>"
            f"<div class='glossary-term'>{escape(term)}</div>"
            f"<div class='glossary-def'>{escape(definition)}</div>"
            "</div>"
        )
    st.markdown("<div class='glossary-grid'>" + "".join(cards) + "</div>", unsafe_allow_html=True)


def render_workflow_strip() -> None:
    steps = [
        ("01", "Collect APIs"),
        ("02", "Clean records"),
        ("03", "Classify signals"),
        ("04", "Generate brief"),
        ("05", "Plan demand"),
    ]
    html = "<div class='workflow'>" + "".join(
        f"<div class='workflow-step'><span>{num}</span>{escape(label)}</div>" for num, label in steps
    ) + "</div>"
    st.markdown(html, unsafe_allow_html=True)


def render_score_chart(feature_df: pd.DataFrame) -> None:
    if feature_df.empty:
        st.info("No feature rows yet.")
        return
    display_levels = feature_df["risk_score"].apply(lambda x: risk_band(float(x or 0)))
    level_values = display_levels.map({"Low": 1, "Medium": 2, "High": 3})
    labels = []
    for i, (_, row) in enumerate(feature_df.iterrows()):
        label = signal_display_label(row, i)
        repeats = labels.count(label) + sum(1 for x in labels if x.startswith(label + " ("))
        labels.append(f"{label} ({repeats + 1})" if repeats else label)
    fig = go.Figure(
        go.Bar(
            x=level_values,
            y=labels,
            orientation="h",
            marker_color=["#22C55E" if x == "Low" else "#F59E0B" if x == "Medium" else "#EF4444" for x in display_levels],
            text=display_levels,
            textposition="auto",
        )
    )
    fig.update_layout(
        height=280,
        margin={"l": 10, "r": 20, "t": 10, "b": 10},
        xaxis={"range": [0, 3.25], "title": "Risk Level", "tickmode": "array", "tickvals": [1, 2, 3], "ticktext": ["Low", "Medium", "High"]},
        yaxis={"title": ""},
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
    )
    st.plotly_chart(fig, width="stretch")


def render_score_explainability(feature_df: pd.DataFrame) -> None:
    if feature_df.empty:
        st.info("No signal explanations available.")
        return
    explanation_rows = feature_df.sort_values("risk_score", ascending=False, kind="mergesort").reset_index(drop=True)
    for _, row in explanation_rows.iterrows():
        score = float(row.get("risk_score", 0) or 0)
        band = risk_band(score)
        # Two weather rows produced byte-identical card titles (one per state), so the
        # same signal appeared twice with opposite verdicts and no way to tell them apart.
        region = str(row.get("region", "") or "").strip()
        title = f"{row.get('signal_area', 'Signal')} · {readable_signal_name(row.get('signal_name', ''))}"
        if region and region.upper() != "US":
            title += f" · {region}"
        meta = f"{row.get('source', 'Unknown source')} / {readable_region_scope(row.get('region_scope', row.get('region', '')))}"
        reason = str(row.get("score_reason") or "No score reason was returned by this collector.")
        evidence = str(row.get("raw_reference") or row.get("signal_value") or "No raw reference available.")
        action = str(row.get("recommended_action") or "Review this signal before using it in planning.")
        formula = scoring_formula_for_row(row)
        html = (
            "<div class='score-explain-card'>"
            "<div class='score-explain-head'>"
            f"<div><div class='score-explain-title'>{escape(title)}</div><div class='score-explain-meta'>{escape(meta)}</div></div>"
            f"<div class='score-number level-{escape(band.lower())}'>{escape(band)}</div>"
            "</div>"
            "<div class='score-explain-label'>Classification rule</div>"
            f"<div class='score-explain-text'>{escape(formula)}</div>"
            "<div class='score-explain-label'>Why this level</div>"
            f"<div class='score-explain-text'>{escape(reason)}</div>"
            "<div class='score-explain-label'>Evidence used</div>"
            f"<div class='score-explain-text'>{escape(evidence)}</div>"
            "<div class='score-explain-label'>Planning action</div>"
            f"<div class='score-explain-text'>{escape(action)}</div>"
            "</div>"
        )
        st.markdown(html, unsafe_allow_html=True)
