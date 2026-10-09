import re
from html import escape
from market_intelligence.ui.operational_impact import replenishment_columns
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
import pandas as pd
import streamlit as st

from market_intelligence.config.settings import DEFAULT_NVIDIA_MODEL
from market_intelligence.incidents.filters import apply_incident_scope_filters
from market_intelligence.llm.incident import _DC_REPLENISHMENT_FACT_COLUMNS, generate_dc_replenishment_recommendation, generate_incident_explanation
from market_intelligence.recalls.ladder import ladder_entry
from market_intelligence.recalls.matching import candidate_store_statement
from market_intelligence.persistence.operational_store import _json_safe_incident, load_decisions, save_decision
from market_intelligence.signals.labels import readable_evidence_code
from market_intelligence.ui.agents import ANALYST_AGENT, PLANNER_AGENT
from market_intelligence.ui.agents import agent_credit as _agent_credit
from market_intelligence.ui.agents import render_agent_text as _render_explanation
from market_intelligence.ui.components import render_metric_card, style_operational_dataframe
from market_intelligence.ui.views.operational_impact.inbox import kpi_row, stat_row
from market_intelligence.util.hashing import payload_hash
from market_intelligence.util.tables import format_as_of_columns, format_recall_date, format_timestamp
from market_intelligence.weather.inbox_helpers import _weather_duration


CONFIDENCE_CHIP = {"high": "chip-low", "medium": "chip-medium"}
URGENCY_CHIP = {"high": "chip-high", "medium": "chip-medium", "low": "chip-low"}
ACTION_META_KEYS = ("Action", "Owner", "Urgency", "Status")
ACTION_FIELD_ORDER = ("Reason", "Evidence", "Decision Support", "Rule")


def _chip(label: str, css_class: str = "chip-neutral") -> str:
    return f"<span class='chip {css_class}'>{escape(str(label))}</span>"


def _section_heading(label: str) -> None:
    st.markdown(f"<div class='section-title spaced'>{escape(label)}</div>", unsafe_allow_html=True)


def _kv_list(rows: List[Tuple[str, str]]) -> None:
    """Label / value rows. Values are HTML that the caller has already escaped."""
    body = "".join(
        f"<div class='kv-row'><div class='kv-label'>{escape(label)}</div><div class='kv-value'>{value}</div></div>"
        for label, value in rows
    )
    st.markdown(f"<div class='kv-list'>{body}</div>", unsafe_allow_html=True)


def _evidence_rows(incident: Dict[str, Any]) -> List[Tuple[str, str]]:
    """The facts behind the assessment, grouped so a reader scans five lines, not a table."""
    if incident.get("Type") == "Weather":
        scenario = incident.get("Scenario", {}) or {}
        geography = scenario.get("geography_match", {}) or {}
        alert = incident.get("Evidence", {}).get("alert", {}) or {}
        evaluated = [str(x) for x in (geography.get("evaluated_store_ids", []) or [])]
        matched = [str(x) for x in (geography.get("affected_store_ids", []) or [])]
        confidence = str(geography.get("confidence") or "None")
        method = str(geography.get("method") or "Not evaluated")
        basis = readable_evidence_code(scenario.get("evidence_code") or geography.get("evidence_code"))
        explanation = str(geography.get("explanation") or scenario.get("reason") or "No mapping explanation is available.")
        return [
            ("NOAA alert area", escape(str(alert.get("area_desc") or "Not supplied"))),
            ("Store mapping", f"{escape(method)} &nbsp;{_chip(confidence + ' confidence', CONFIDENCE_CHIP.get(confidence.lower(), 'chip-neutral'))}"),
            ("Stores matched", f"<strong>{len(matched)} of {len(evaluated)}</strong> evaluated &middot; {escape(', '.join(matched) or 'none')}"),
            ("Evidence basis", escape(str(basis))),
            ("Explanation", escape(explanation)),
        ]
    evidence = incident.get("Evidence", {}) or {}
    ladder = (incident.get("Impact Metrics", {}) or {}).get("Evidence ladder")
    recall = " &middot; ".join(escape(str(v)) for v in (evidence.get("recall_number"), evidence.get("classification")) if v)
    candidates = [
        ("Product", escape(str(evidence.get("product") or ""))),
        ("Recall", recall),
        ("FDA recalling firm", "" if evidence.get("is_demo_scenario") else escape(str(evidence.get("recalling_firm") or ""))),
        ("Recall date", "" if evidence.get("is_demo_scenario") or not evidence.get("recall_date") else escape(format_recall_date(evidence.get("recall_date")))),
        ("Reason", escape(str(evidence.get("reason") or ""))),
        ("Distribution", escape(str(evidence.get("distribution_pattern") or ""))),
        ("Match level", escape(str(ladder or ""))),
        ("UPCs", escape(", ".join(map(str, evidence.get("upcs") or [])))),
        ("Lots", escape(", ".join(map(str, evidence.get("lots") or [])))),
    ]
    return [(label, value) for label, value in candidates if value]


def _estimated_unit_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Label lot-level inventory as estimated whole units rather than observed physical counts."""
    labels = {"On Hand": "Est. On Hand (units)", "In Transit": "Est. In Transit (units)"}
    return frame.rename(columns={k: v for k, v in labels.items() if k in frame.columns})


def _render_probable_estimate(match: Dict[str, Any]) -> None:
    """Planning-only stage 2 for a probable product match; never presented as confirmed exposure."""
    _section_heading("Probable product match \u2014 estimated planning visibility")
    st.caption(
        "A probable product match was identified. The displayed Store/DC quantities are planning estimates only; confirm the UPC and lot before treating them as affected inventory or initiating action."
    )
    if not match.get("candidate_estimate_available"):
        st.caption("Distribution is not stated or cannot be resolved, so no store estimate is shown.")
        return
    st.markdown(f"**{escape(candidate_store_statement(match))}**")
    kpi_row(
        [
            ("Candidate product", str(match.get("candidate_product", "")), ""),
            ("Footprint stores", str(int(match.get("footprint_store_count", 0) or 0)), ""),
            ("UPC/Lot-Confirmed Affected Stores", "0", ""),
        ]
    )
    st.markdown(
        "<div class='fact-line'>"
        f"Internal Candidate UPC (Synthetic) <strong>{escape(str(match.get('candidate_upc', '')))}</strong>"
        + (f" &middot; Brand <strong>{escape(str(match['candidate_brand']))}</strong>" if match.get("candidate_brand") else "")
        + (f" &middot; Vendor <strong>{escape(str(match['candidate_vendor']))}</strong>" if match.get("candidate_vendor") else "")
        + (f" &middot; Package size <strong>{escape(str(match['candidate_package_size']))}</strong>" if match.get("candidate_package_size") else "")
        + f" &middot; Brand/vendor check <strong>{'found' if match.get('brand_matched') else 'not found'}</strong>"
        + f" &middot; Package size check <strong>{'found' if match.get('size_matched') else 'not found'}</strong>"
        +
        f" &middot; Estimated store inventory <strong>{int(match.get('estimated_store_on_hand', 0)):,} units</strong>"
        f" &middot; Estimated DC inventory <strong>{int(match.get('estimated_dc_on_hand', 0)):,} units</strong>"
        f" &middot; Estimated in transit <strong>{int(match.get('estimated_dc_in_transit', 0)):,} units</strong>"
        " &middot; UPC/lot confirmation required"
        "</div>",
        unsafe_allow_html=True,
    )
    st.caption("Product-level stock in the distribution states; internal assortment and inventory are synthetic. Withdrawal stays disabled.")


def _recall_ladder_rows(incident: Dict[str, Any]) -> List[Tuple[str, str]]:
    """What this evidence level proves, what is only estimated, what is still needed and whether stores may act."""
    match = incident.get("Match", {}) or {}
    entry = ladder_entry(match.get("match_status") or incident.get("Status"))
    footprint = (
        f"{int(match.get('footprint_store_count', 0) or 0)} candidate store(s) in the distribution states "
        "(candidates, not confirmed affected)"
        if match.get("distribution_known")
        else "Distribution is not stated or cannot be resolved"
    )
    return [
        ("Evidence level", escape(str(entry["level"]))),
        ("Confirmed", escape(str(entry["confirmed"]))),
        ("Estimated", escape(str(entry["estimated"]))),
        ("Still required", escape(str(entry["required"]))),
        ("Store action allowed", "Yes" if entry["store_action"] else "No"),
        ("Permitted action", escape(str(entry["action"]))),
        ("Distribution footprint", escape(footprint)),
    ]


def _recommendation_cards(actions: List[Dict[str, Any]]) -> None:
    cards = []
    for action in actions:
        urgency = str(action.get("Urgency", "") or "").strip()
        chips = ""
        if urgency and urgency.lower() != "none":
            chips += _chip(f"{urgency} urgency", URGENCY_CHIP.get(urgency.lower(), "chip-neutral"))
        for key in ("Owner", "Status"):
            if str(action.get(key, "") or "").strip():
                chips += _chip(str(action[key]))
        ordered = [k for k in ACTION_FIELD_ORDER if k in action] + [
            k for k in action if k not in ACTION_META_KEYS and k not in ACTION_FIELD_ORDER
        ]
        fields = "".join(
            f"<div class='rec-field'><div class='rec-label'>{escape(str(key))}</div>"
            f"<div class='rec-text'>{escape(str(action[key]))}</div></div>"
            for key in ordered
            if str(action.get(key, "") or "").strip()
        )
        cards.append(
            "<div class='rec-card'>"
            f"<div class='rec-head'><div class='rec-title'>{escape(str(action.get('Action', 'Action')))}</div>"
            f"<div class='rec-chips'>{chips}</div></div>{fields}</div>"
        )
    st.markdown("".join(cards), unsafe_allow_html=True)



def _phase_cards(traffic_df: pd.DataFrame) -> None:
    """Pre-event / during / post-event customer traffic as three cards in a row."""
    cards = []
    for _, row in traffic_df.iterrows():
        try:
            change = f"{float(row.get('Customer Store Traffic %', 0)):+.1f}%"
        except (TypeError, ValueError):
            change = str(row.get("Customer Store Traffic %", ""))
        phase = str(row.get("Phase", "")).replace("-", " ").capitalize()
        cards.append(
            "<div class='phase-card'>"
            f"<div class='phase-name'>{escape(phase)}</div>"
            f"<div class='phase-value'>{escape(change)} <span class='phase-sub'>{escape(str(row.get('Traffic vs Normal (multiplier)', '')))} normal</span></div>"
            f"<div class='phase-text'>{escape(str(row.get('What This Means', '')))}</div>"
            "</div>"
        )
    st.markdown(f"<div class='phase-row'>{''.join(cards)}</div>", unsafe_allow_html=True)


def _render_replenishment_planner(incident: Dict[str, Any], nvidia_key: str, nvidia_model: str, key_prefix: str = "") -> None:
    """Per-store DC coverage recommendations from the agent, for every store with a gap in the incident."""
    _section_heading(PLANNER_AGENT)
    st.caption(
        "Recommends how to cover each store's shortfall from the DC network.",
        help=(
            "Uses only the numbers in the replenishment tables on the Impact tab and never adds new facts. If the agent is "
            "unavailable, or its answer cannot be verified against those numbers, a rule-based summary of the "
            "same numbers is shown instead."
        ),
    )
    plan_df = (incident.get("Scenario", {}) or {}).get("scenario_df")
    if not isinstance(plan_df, pd.DataFrame) or "Order Quantity" not in plan_df.columns or "Store ID" not in plan_df.columns:
        st.caption("No store or product rows are available for a replenishment recommendation.")
        return
    stores_with_gap = plan_df[pd.to_numeric(plan_df["Order Quantity"], errors="coerce").fillna(0) > 0]
    if stores_with_gap.empty:
        st.caption("No open inventory gap in this incident.")
        return
    fingerprint = payload_hash(
        stores_with_gap[
            [c for c in _DC_REPLENISHMENT_FACT_COLUMNS + ("Store ID", "Store Name") if c in stores_with_gap.columns]
        ].to_dict("records")
    )
    state_key = f"{key_prefix}dc_replenishment_recs_{incident.get('Incident ID', '')}"
    cached = st.session_state.get(state_key)
    if cached and cached.get("fingerprint") != fingerprint:
        st.info(f"The inputs changed since the last plan. Run the {PLANNER_AGENT} again to refresh it.")
    if st.button(f"Run {PLANNER_AGENT}", key=f"{state_key}_button"):
        recommendations: Dict[str, Dict[str, str]] = {}
        for store_id, store_rows in stores_with_gap.groupby("Store ID"):
            store_name = str(store_rows.iloc[0].get("Store Name", store_id))
            text, source = generate_dc_replenishment_recommendation(nvidia_key, nvidia_model, store_name, store_rows)
            recommendations[str(store_id)] = {"store_name": store_name, "text": text, "source": source}
        st.session_state[state_key] = {"fingerprint": fingerprint, "recommendations": recommendations}
        cached = st.session_state[state_key]
    if cached and cached.get("fingerprint") == fingerprint:
        for rec in cached["recommendations"].values():
            with st.expander(rec["store_name"], expanded=True):
                st.write(rec["text"])
                st.caption(_agent_credit(PLANNER_AGENT, rec["source"]))


def _alert_timing(metrics: Dict[str, Any]) -> str:
    """Rounded the same way the inbox rounds it, so the two screens never disagree."""
    if str(metrics.get("Alert status", "")).lower() == "active now":
        return f"Active now, ends in {_weather_duration(float(metrics.get('Hours until alert ends', 0) or 0))}"
    return f"Starts in {_weather_duration(float(metrics.get('Hours until alert window', 0) or 0))}"


# The two assistants in an incident, shown by role rather than by the model behind them.
STATUS_TEXT_STYLE = {
    "Escalate": "color:#B91C1C;font-weight:700",
    "Covered": "color:#B45309;font-weight:700",
    "No gap": "color:#15803D;font-weight:700",
}
STATUS_ORDER = {"Escalate": 0, "Covered": 1, "No gap": 2}
DEMAND_COLUMNS = ["Status", "Store", "Product", "Gap", "Order Quantity", "Quantity Rounding", "Baseline", "Projected Demand", "On Hand", "Inbound", "UPC", "Units per Case", "MOQ (units)"]
DC_COLUMNS = ["Status", "Store", "Product", "Primary Planned Qty", "Backup DC", "Backup Planned Qty", "Residual Gap", "Primary DC", "Primary ATP", "Backup DC Status", "Backup ATP", "Route Risk"]
TOTAL_COLUMNS = ("Gap", "Order Quantity", "Primary Planned Qty", "Backup Planned Qty", "Residual Gap")
REPLENISHMENT_COLUMN_CONFIG = {
    "Status": st.column_config.TextColumn(width=78),
    "Store": st.column_config.TextColumn(width=118),
    "Product": st.column_config.TextColumn(width=112),
    "Primary DC": st.column_config.TextColumn(width=135),
    "Backup DC": st.column_config.TextColumn(width=135),
    "Quantity Rounding": st.column_config.TextColumn(width=300),
}


def _store_label(store_id: str, store_name: str) -> str:
    """"101" and "Store 101 - Dallas, TX" become "101 · Dallas, TX"."""
    place = re.sub(r"^Store\s*\d+\s*-\s*", "", store_name)
    return f"{store_id} · {place}".strip(" ·")


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce").fillna(0)
    return pd.Series(0.0, index=frame.index)


def _replenishment_frame(weather_df: pd.DataFrame, show_uplift: bool) -> pd.DataFrame:
    """One row per store and product with an escalation status, ordered worst first.

    Status: "Escalate" when a shortfall is still uncovered after primary and backup DC allocation
    (Residual Gap above zero), "Covered" when there is a shortfall that the DCs cover, and
    "No gap" otherwise.
    """
    frame = weather_df.copy().reset_index(drop=True)
    residual = _numeric(frame, "Residual Gap")
    needed = (_numeric(frame, "Order Quantity") > 0) | (_numeric(frame, "Forecast Shortfall") > 0)
    frame["Status"] = ["Escalate" if r > 0 else "Covered" if n else "No gap" for r, n in zip(residual, needed)]
    store_id = frame["Store ID"].astype(str) if "Store ID" in frame.columns else pd.Series("", index=frame.index)
    store_name = frame["Store Name"].astype(str) if "Store Name" in frame.columns else pd.Series("", index=frame.index)
    frame["Store"] = [_store_label(sid, name) for sid, name in zip(store_id, store_name)]
    frame = frame.rename(
        columns={
            "Forecast Shortfall": "Gap",
            "Baseline Forecast": "Baseline",
            "Case Pack": "Units per Case",
            "MOQ": "MOQ (units)",
            "Scenario Demand Assumption %": "Uplift %",
            "Primary DC Available ATP": "Primary ATP",
            "Backup DC Available ATP": "Backup ATP",
        }
    )
    if not show_uplift and "Uplift %" in frame.columns:
        frame = frame.drop(columns=["Uplift %"])
    frame["_rank"] = frame["Status"].map(STATUS_ORDER)
    sort_keys = ["_rank"] + [c for c in ("Store ID", "Product") if c in frame.columns]
    return frame.sort_values(sort_keys, kind="stable").drop(columns="_rank").reset_index(drop=True)


SCENARIO_ONLY_LABEL = "Demonstration only \u2014 no operational execution"


def source_classification(incident: Dict[str, Any], scenario_view: bool = False, override: str = "") -> str:
    """Where an incident came from, so a synthetic scenario is never mistaken for a live event."""
    if override:
        return override
    incident_type = str(incident.get("Type", ""))
    controlled = scenario_view or bool((incident.get("Evidence", {}) or {}).get("is_demo_scenario")) or (
        "Demonstration" in str(incident.get("Source", ""))
    )
    if controlled:
        return "Controlled Product Recall Scenario" if incident_type == "Product Recall" else "Controlled Weather Scenario"
    return "Live openFDA" if incident_type == "Product Recall" else "Live NOAA"


def no_split_notes(frame: pd.DataFrame, limit: int = 3) -> List[str]:
    """Explain rows where stock exists but no single DC can take the whole order.

    One product is never split across two DCs, so when neither the Primary nor the Backup DC can
    cover the complete rounded order the plan allocates nothing and the whole quantity is escalated.
    That looks like a calculation error unless it is said out loud.
    """
    needed = {"Order Quantity", "Primary Planned Qty", "Backup Planned Qty", "Residual Gap"}
    if frame is None or frame.empty or not needed.issubset(frame.columns):
        return []
    order = _numeric(frame, "Order Quantity")
    planned = _numeric(frame, "Primary Planned Qty") + _numeric(frame, "Backup Planned Qty")
    residual = _numeric(frame, "Residual Gap")
    available = _numeric(frame, "Primary ATP") + _numeric(frame, "Backup ATP")
    hit = frame[(order > 0) & (planned == 0) & (residual > 0) & (available > 0)]
    notes: List[str] = []
    for _, row in hit.head(limit).iterrows():
        units = float(pd.to_numeric(row.get("Residual Gap"), errors="coerce") or 0)
        where = f"{row.get('Product', 'This product')} at {row.get('Store', 'this store')}".strip()
        notes.append(
            f"{where}: neither the Primary nor Backup DC can cover the complete rounded order quantity. "
            f"Under the single-DC-per-product rule, no partial allocation is made, so the complete {units:,.0f}-unit "
            "order remains escalated."
        )
    if len(hit) > limit:
        notes.append(f"{len(hit) - limit} more product row(s) follow the same single-DC-per-product rule.")
    return notes


def _render_replenishment_table(frame: pd.DataFrame, columns: List[str]) -> None:
    """A status-coloured table: whole-row tint, store shown once per run of rows, bold total row."""
    shown = [c for c in columns if c in frame.columns]
    if "Uplift %" in frame.columns and "Uplift %" not in shown:
        shown.append("Uplift %")
    table = frame[shown].copy()
    # Store stays on every row. The table's CSV download is a copy of what is shown, and a
    # row that depends on the row above it for its Store is not usable once exported.
    total_row = {c: ("" if table[c].dtype == object else float("nan")) for c in table.columns}
    if "Store" in total_row:
        total_row["Store"] = "Total"
    for column in TOTAL_COLUMNS:
        if column in table.columns:
            total_row[column] = float(pd.to_numeric(table[column], errors="coerce").fillna(0).sum())
    table = pd.concat([table, pd.DataFrame([total_row])], ignore_index=True)
    last_row = len(table) - 1
    styler = style_operational_dataframe(table, row_level_column="Status", emphasise_cells=False)
    style_map = styler.map if hasattr(styler, "map") else styler.applymap
    if "Status" in table.columns:
        styler = style_map(lambda value: STATUS_TEXT_STYLE.get(str(value), ""), subset=["Status"])
    styler = styler.apply(
        lambda row: ["font-weight:700;background-color:#F1F5F9"] * len(row) if row.name == last_row else [""] * len(row),
        axis=1,
    )
    st.dataframe(
        styler,
        width="stretch",
        hide_index=True,
        height=min(35 * (len(table) + 1) + 3, 600),
        column_config=REPLENISHMENT_COLUMN_CONFIG,
    )


def _column_total(df: Any, column: str) -> float:
    if not isinstance(df, pd.DataFrame) or column not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[column], errors="coerce").fillna(0).sum())


def render_incident_detail_panel(
    selected_incident: Optional[Dict[str, Any]],
    selected_id: str,
    created_at: str,
    nvidia_key: str,
    nvidia_model: str,
    scenario_view: bool = False,
    key_prefix: str = "",
    source_label: str = "",
) -> None:
    """Shared Impact/Evidence/Recommended Actions/Decision History detail view for one
    incident -- used by both the Weather and Product Recall tabs so the two stay visually
    separate (different inboxes/filters/generators) without duplicating this rendering."""
    if selected_incident is None:
        st.markdown('<div class="small-header">Incident detail</div>', unsafe_allow_html=True)
        st.warning("This incident's stored detail could not be loaded.")
        history_df = load_decisions(selected_id)
        if not history_df.empty:
            st.dataframe(style_operational_dataframe(history_df), width="stretch", hide_index=True)
        return
    priority_text = str(selected_incident.get("Priority", "") or "").strip()
    incident_type = str(selected_incident.get("Type", "Incident"))
    event_name = str(selected_incident.get("Event", "") or "").strip()
    title = event_name if incident_type == "Weather" and event_name else f"{incident_type} · {event_name}".strip(" ·")
    # The geography-join method is evidence, not a headline: keep the scope readable.
    scope_text = re.sub(r"\s*\([^)]*join[^)]*\)", "", str(selected_incident.get("Affected Scope", "") or "")).strip()
    chips = []
    if priority_text:
        chips.append(f"<span class='chip chip-{escape(priority_text.lower())}'>{escape(priority_text)} priority</span>")
    impact_for_header = selected_incident.get("Impact Metrics") or {}
    if incident_type == "Weather" and selected_incident.get("Status") == "ok" and impact_for_header:
        chips.append(f"<span class='chip chip-neutral'>{escape(_alert_timing(impact_for_header))}</span>")
    st.markdown(
        "<div class='detail-head'>"
        f"<div class='detail-title'>{escape(title)}</div>"
        f"<div class='detail-sub'>{''.join(chips)}"
        f"<span>{escape(str(selected_incident.get('Incident ID', selected_id)))}</span>"
        f"{(' &middot; <span>' + escape(scope_text) + '</span>') if scope_text else ''}</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    provenance = selected_incident.get("Provenance", {})
    st.markdown(
        "<div class='confidence-line'>"
        f"External source: <strong>{escape(str(provenance.get('external_signal_mode', 'Scenario' if scenario_view else 'Unknown')))}</strong>"
        " &middot; Internal operations: <strong>synthetic fixtures v1</strong>"
        f" &middot; Assessed {escape(format_timestamp(selected_incident.get('Run ID', created_at)) or str(created_at))}"
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div class='confidence-line'>Source classification: "
        f"<strong>{escape(source_classification(selected_incident, scenario_view, source_label))}</strong>"
        f"{(' &middot; ' + escape(SCENARIO_ONLY_LABEL)) if scenario_view else ''}</div>",
        unsafe_allow_html=True,
    )
    if selected_incident.get("Evidence", {}).get("is_demo_scenario"):
        st.warning("This incident is a controlled demonstration scenario -- not live NOAA/FDA data.")

    impact_tab, evidence_tab, decision_tab = st.tabs(["Impact", "Evidence & recommendations", "Decision History"])
    with impact_tab:
        if selected_incident["Impact Metrics"]:
            if selected_incident["Type"] == "Weather" and selected_incident["Status"] == "ok":
                # Keep the top Weather Impact summary concise and decision-oriented.
                # Detailed staffing, product, inventory, DC and route data are still
                # shown in the existing tables below.
                metrics = selected_incident["Impact Metrics"]
                scheduled_staff = int(metrics.get("Scheduled staff", 0) or 0)
                staff_at_risk = int(metrics.get("Staff commute at risk", 0) or 0)
                staff_available = int(metrics.get("Expected staff available", 0) or 0)

                incident_df = selected_incident.get("Scenario", {}).get("scenario_df")
                units_to_order = round(_column_total(incident_df, "Order Quantity"))
                remaining_gap = round(_column_total(incident_df, "Residual Gap"))
                kpi_row(
                    [
                        ("Stores affected", f"{int(metrics.get('Affected stores', 0) or 0):,}", ""),
                        ("Stores with an inventory gap", f"{int(metrics.get('Stores with inventory gap', 0) or 0):,}", ""),
                        ("Units to order", f"{units_to_order:,}", ""),
                        ("Remaining gap after DC allocation", f"{remaining_gap:,} units", "bad" if remaining_gap > 0 else "good"),
                    ]
                )
                st.markdown(
                    "<div class='fact-line'>"
                    f"Products with a gap <strong>{int(metrics.get('Products with inventory gap', 0) or 0)}</strong>"
                    f" &middot; Stores with route risk <strong>{int(metrics.get('Stores with route risk', 0) or 0)}</strong>"
                    f" &middot; Staffing <strong>{staff_available} of {scheduled_staff}</strong> available ({staff_at_risk} at risk)"
                    f" &middot; Comparable scenario cases <strong>{int(metrics.get('Comparable scenario cases', 0) or 0)}</strong>"
                    "</div>",
                    unsafe_allow_html=True,
                )
            else:
                metrics = selected_incident["Impact Metrics"]
                stat_row(metrics)

        else:
            st.warning(selected_incident["Limitations"][0] if selected_incident["Limitations"] else "No impact calculated.")
        if selected_incident["Type"] == "Weather" and selected_incident["Status"] == "ok":
            scenario = selected_incident["Scenario"]
            weather_df = scenario.get("scenario_df", pd.DataFrame())

            # --------------------------------------------------------------
            # 0) Store / product / DC filters
            # With production data spanning many Stores and products, the tables
            # below need to stay usable. Filtering here (rather than in each table
            # separately) keeps the summary cards and both detail tables consistent
            # with one another, which the overall review flagged as a requirement.
            # --------------------------------------------------------------
            filter_key_prefix = f"{key_prefix}weather_impact_filter_{selected_incident.get('Incident ID', '')}"
            store_filter: List[str] = []
            product_filter: List[str] = []
            primary_dc_filter: List[str] = []
            backup_dc_filter: List[str] = []
            if not weather_df.empty:
                f1, f2, f3, f4 = st.columns(4)
                with f1:
                    store_options = sorted(weather_df["Store ID"].dropna().astype(str).unique().tolist())
                    store_filter = st.multiselect("Store", store_options, key=f"{filter_key_prefix}_store", placeholder="All stores", label_visibility="collapsed")
                with f2:
                    product_options = sorted(weather_df["Product"].dropna().astype(str).unique().tolist()) if "Product" in weather_df.columns else []
                    product_filter = st.multiselect("Product", product_options, key=f"{filter_key_prefix}_product", placeholder="All products", label_visibility="collapsed")
                with f3:
                    primary_dc_options = sorted(weather_df["Primary DC"].dropna().astype(str).unique().tolist()) if "Primary DC" in weather_df.columns else []
                    primary_dc_filter = st.multiselect("Primary DC", primary_dc_options, key=f"{filter_key_prefix}_primary_dc", placeholder="All primary DCs", label_visibility="collapsed")
                with f4:
                    # OIC-5/OIC-1: exclude by the explicit "Backup DC Status" column
                    # (Ineligible/No alternate), not a name-string pattern -- the DC name
                    # itself is now always clean (see OIC-1), so a substring check against
                    # it could never catch a non-eligible row again.
                    if "Backup DC" in weather_df.columns:
                        if "Backup DC Status" in weather_df.columns:
                            eligible_names = weather_df.loc[
                                weather_df["Backup DC Status"] == "Eligible", "Backup DC"
                            ]
                        else:
                            eligible_names = weather_df["Backup DC"]
                        backup_dc_options = sorted(eligible_names.dropna().astype(str).unique().tolist())
                    else:
                        backup_dc_options = []
                    backup_dc_filter = st.multiselect("Backup DC", backup_dc_options, key=f"{filter_key_prefix}_backup_dc", placeholder="All backup DCs", label_visibility="collapsed")
                weather_df = apply_incident_scope_filters(
                    weather_df,
                    store_filter=store_filter,
                    product_filter=product_filter,
                    primary_dc_filter=primary_dc_filter,
                    backup_dc_filter=backup_dc_filter,
                )
                if any([store_filter, product_filter, primary_dc_filter, backup_dc_filter]) and weather_df.empty:
                    st.info("No Store/product rows match the selected filters.")

            # --------------------------------------------------------------
            # Replenishment by store and product
            # Two status-coloured tables side by side: demand and inventory (what is short, what to
            # order, how the quantity was calculated) next to the DC plan (which DC covers it and what
            # is still open). Rows are shared, so colour and order match across the two.
            # Review requirement (Case pack and MOQ): Gap, Units per Case, MOQ and the final Order
            # Quantity stay separate columns, with Quantity Rounding stating which rule produced
            # the Order Quantity.
            # --------------------------------------------------------------
            _section_heading("Replenishment by store and product")
            # The uplift is one published assumption for the whole event, so it is stated once
            # instead of repeating the same number on every row.
            uplift_values = sorted(
                {
                    round(float(value), 1)
                    for value in pd.to_numeric(
                        weather_df.get("Scenario Demand Assumption %", pd.Series(dtype=float)),
                        errors="coerce",
                    ).dropna()
                }
            )
            if len(uplift_values) == 1:
                st.caption(
                    f"Demand uplift of +{uplift_values[0]:.1f}% applied to every product.",
                    help=f"Assumption set {scenario.get('assumption_version', 'scenario-assumptions-v1')}.",
                )
            if weather_df.empty:
                st.caption("No store or product rows to show.")
            else:
                replenishment_frame = _replenishment_frame(weather_df, show_uplift=len(uplift_values) > 1)
                left_col, right_col = st.columns(2, gap="medium")
                with left_col:
                    st.markdown("<div class='section-title'>Demand and inventory</div>", unsafe_allow_html=True)
                    _render_replenishment_table(replenishment_frame, DEMAND_COLUMNS)
                with right_col:
                    st.markdown("<div class='section-title'>DC replenishment</div>", unsafe_allow_html=True)
                    _render_replenishment_table(replenishment_frame, DC_COLUMNS)
                for note in no_split_notes(replenishment_frame):
                    st.caption(note)
            st.caption(
                "Escalate: shortfall uncovered after DC allocation  ·  Covered: DCs cover the shortfall  ·  No gap: nothing to order. "
                "Recommended actions and the inbox plan are built from these table totals, not recalculated.",
                help=(
                    "Primary DC is the normal store-to-DC mapping and Primary Planned Qty is what it can supply for this "
                    "incident. If that is short or zero, Backup DC shows the eligible alternate. Residual Gap is what is "
                    "still uncovered after primary and backup allocation."
                ),
            )
            inventory_as_of_values = sorted(
                {
                    str(value).strip()
                    for value in weather_df.get("Store Inventory As Of", pd.Series(dtype=str)).dropna()
                    if str(value).strip()
                }
            )
            if inventory_as_of_values:
                as_of_display = format_as_of_columns(
                    pd.DataFrame({"Store Inventory As Of": inventory_as_of_values})
                )["Store Inventory As Of"].tolist()
                st.caption(
                    f"Inventory snapshot: {', '.join(as_of_display)} (synthetic data).",
                    help=(
                        "The snapshot is a synthetic fixture and is not refreshed to today's date automatically. "
                        "Order Quantity already accounts for each product's case pack and MOQ; see Quantity Rounding for the rule applied."
                    ),
                )
            # Store filter scopes staffing/employee records (both are per-Store data);
            # Product and Primary/Backup DC filters must not -- staffing is not
            # product- or DC-scoped data, so filtering it on either would silently
            # drop staff who have nothing to do with the selected product or DC.
            staffing_df = apply_incident_scope_filters(
                scenario.get("staffing_df", pd.DataFrame()), store_filter=store_filter, dimensions=("store",)
            )
            if isinstance(staffing_df, pd.DataFrame) and not staffing_df.empty:
                _section_heading("Staffing readiness")
                st.caption(
                    "Based on the alert-window schedule and employee commute exposure.",
                    help=(
                        "Staffing risk uses the current alert-window schedule and tokenized employee home ZIP/county commute exposure. "
                        "Recent 14-day callouts and historical employees are not used to classify the current staffing risk."
                    ),
                )
                scheduled_total = int(staffing_df["Scheduled Staff"].sum())
                at_risk_total = int(staffing_df["At-risk Commute Staff"].sum())
                available_total = int(staffing_df["Expected Available Before Alert"].sum())
                backup_total = int(staffing_df["On-call Backup Staff"].sum()) if "On-call Backup Staff" in staffing_df.columns else 0
                risk_values = staffing_df["Staffing Risk"].astype(str).tolist()
                overall_staffing_risk = "High" if "High" in risk_values else "Elevated" if "Elevated" in risk_values else "Low"
                operating_capacity_pct = round((available_total / scheduled_total) * 100) if scheduled_total else 0
                kpi_row(
                    [
                        ("Scheduled staff", str(scheduled_total), ""),
                        ("Commute at risk", str(at_risk_total), "bad" if at_risk_total else ""),
                        ("Expected available", str(available_total), ""),
                        ("On-call backup", str(backup_total), ""),
                        ("Staffing risk", overall_staffing_risk, {"High": "bad", "Elevated": "warn", "Low": "good"}.get(overall_staffing_risk, "")),
                    ]
                )
                st.caption(
                    f"Expected staffing capacity {operating_capacity_pct}% before backup activation.",
                    help="Based on the current schedule and commute exposure, not on historical staff.",
                )
                staff_display = staffing_df.copy()
                if "Commute Risk %" in staff_display.columns:
                    staff_display["Commute Risk %"] = (pd.to_numeric(staff_display["Commute Risk %"], errors="coerce").fillna(0) * 100).round(0).astype(int).astype(str) + "%"
                staff_first = ["Store ID", "Store Name", "Staffing Risk", "Staffing Action", "Scheduled Staff", "At-risk Commute Staff", "Expected Available Before Alert", "On-call Backup Staff"]
                staff_order = [c for c in staff_first if c in staff_display.columns]
                staff_order += [c for c in staff_display.columns if c not in staff_order]
                st.dataframe(
                    style_operational_dataframe(staff_display[staff_order], row_level_column="Staffing Risk"),
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Store Name": st.column_config.TextColumn(width=170),
                        "Staffing Risk": st.column_config.TextColumn(width=105),
                        "Staffing Action": st.column_config.TextColumn(width=330),
                    },
                )

            employee_df = apply_incident_scope_filters(
                scenario.get("employee_detail_df", pd.DataFrame()), store_filter=store_filter, dimensions=("store",)
            )
            if isinstance(employee_df, pd.DataFrame) and not employee_df.empty:
                with st.expander("Scheduled employee commute detail (tokenised illustrative data)"):
                    st.dataframe(
                        style_operational_dataframe(employee_df), width="stretch", hide_index=True
                    )

            traffic_df = scenario.get("traffic_df", pd.DataFrame())
            if isinstance(traffic_df, pd.DataFrame) and not traffic_df.empty:
                _section_heading("Customer store traffic")
                st.caption(
                    "Customer store-visit traffic (footfall) around the event, not road traffic.",
                    help=(
                        "The typical pattern is a pre-event buying surge, a drop while the event is active, "
                        "then a post-event rebound as customers restock."
                    ),
                )
                _phase_cards(traffic_df)
        if selected_incident["Type"] == "Product Recall":
            match = selected_incident.get("Match", {})
            if match.get("match_status") == "product_review_required":
                _render_probable_estimate(match)
            if match.get("store_lines"):
                _section_heading("Store-level exposure")
                st.dataframe(style_operational_dataframe(_estimated_unit_columns(pd.DataFrame(match["store_lines"]))), width="stretch", hide_index=True)
                st.caption(
                    "Lot quantities are estimated allocations of product-level inventory, in whole units that add up to the totals. "
                    + str(match.get("financial_exposure_derivation", ""))
                )
            if match.get("dc_lines"):
                _section_heading("DC-level exposure")
                st.dataframe(style_operational_dataframe(_estimated_unit_columns(pd.DataFrame(match["dc_lines"]))), width="stretch", hide_index=True)
            if match.get("substitute_product"):
                _section_heading(f"Substitute readiness: {match['substitute_product']}")
                if match.get("substitute_readiness"):
                    st.dataframe(style_operational_dataframe(pd.DataFrame(match["substitute_readiness"])), width="stretch", hide_index=True)
                else:
                    st.caption("Substitute is mapped but not carried at any affected store in the reference inventory.")
            elif match.get("match_status") in ("confirmed_exact", "confirmed_upc_only"):
                st.caption("No substitute product mapped for this item in the demonstration catalog.")
            if match.get("supplier_name"):
                supplier_note = f"Internal catalog supplier: {match['supplier_name']} -- {match.get('supplier_prior_recalls', 0)} prior recall(s)."
                if match.get("supplier_review_required"):
                    st.warning(supplier_note + " Supplier review required.")
                else:
                    st.caption(supplier_note)
                st.caption("The FDA recalling firm and the retailer's internal supplier may be different entities.")
        if selected_incident["Limitations"]:
            with st.expander(f"Assumptions & limitations ({len(selected_incident['Limitations'])})"):
                for limitation in selected_incident["Limitations"]:
                    st.caption(limitation)
    with evidence_tab:
        _section_heading("Evidence")
        evidence_rows = _evidence_rows(selected_incident)
        if evidence_rows:
            _kv_list(evidence_rows)
        if selected_incident["Type"] == "Product Recall":
            _kv_list(_recall_ladder_rows(selected_incident))
        if selected_incident["Type"] == "Weather" and "county" in str(
            (selected_incident.get("Scenario", {}) or {}).get("geography_match", {}).get("method", "")
        ).lower():
            st.caption("A county-name match is a low-confidence text match, not a NOAA polygon intersection.")
        # Collapsed by default: it is the audit record behind the evidence, not the first thing to read.
        with st.expander("Underlying source record for this incident", expanded=False):
            st.json(selected_incident["Evidence"])

        _section_heading("Recommended actions")
        if selected_incident["Recommended Actions"]:
            _recommendation_cards(selected_incident["Recommended Actions"])
        else:
            st.info("No recommended action for this incident.")

        if selected_incident["Type"] == "Weather" and selected_incident.get("Status") == "ok":
            _render_replenishment_planner(selected_incident, nvidia_key, nvidia_model, key_prefix)

        _section_heading(ANALYST_AGENT)
        # The explanation is lazy-loaded: Streamlit runs the body of every tab on each rerun, so
        # calling the model here unprompted would slow every incident switch.
        explanation_key = (
            f"incident_explanation::{selected_id}::{selected_incident.get('Run ID', '')}::{payload_hash(_json_safe_incident(selected_incident))[:12]}::"
            f"{nvidia_model or DEFAULT_NVIDIA_MODEL}"
        )
        cached_explanation = st.session_state.get(explanation_key)

        if cached_explanation:
            explanation, explanation_source = cached_explanation
            with st.container(border=True):
                _render_explanation(explanation)
                st.caption(_agent_credit(ANALYST_AGENT, explanation_source))

            if st.button(
                f"Re-run {ANALYST_AGENT}",
                key=f"{key_prefix}refresh_incident_explanation_{selected_id}",
            ):
                with st.spinner(f"{ANALYST_AGENT} is working..."):
                    explanation, explanation_source = generate_incident_explanation(
                        nvidia_key,
                        nvidia_model,
                        selected_incident,
                    )
                st.session_state[explanation_key] = (
                    explanation,
                    explanation_source,
                )
                st.rerun()
        else:
            st.caption(
                "Explains the evidence and the recommended actions in plain language, using only the facts calculated above.",
                help="Run on request so that switching between incidents stays fast.",
            )
            if st.button(
                f"Ask the {ANALYST_AGENT} to explain",
                key=f"{key_prefix}generate_incident_explanation_{selected_id}",
            ):
                with st.spinner(f"{ANALYST_AGENT} is working..."):
                    explanation, explanation_source = generate_incident_explanation(
                        nvidia_key,
                        nvidia_model,
                        selected_incident,
                    )
                st.session_state[explanation_key] = (
                    explanation,
                    explanation_source,
                )
                st.rerun()
    with decision_tab:
        if scenario_view:
            st.info("Scenario decisions are not operational approvals. Use the proposed actions for walkthrough only.")
            return
        history_df = load_decisions(selected_id)
        if not history_df.empty:
            st.dataframe(style_operational_dataframe(history_df), width="stretch", hide_index=True)
        else:
            st.info("No planner decision recorded yet.")
        with st.form(f"decision_form_{selected_id}"):
            actor = st.text_input("Your name")
            decision = st.selectbox("Decision", ["Approved", "Modified", "Rejected"])
            modified_action = st.text_input("Modified action (only if Modified)", value="")
            reason = st.text_area("Reason (required)")
            submitted = st.form_submit_button("Record decision")
            if submitted:
                if decision == "Modified" and not modified_action.strip():
                    st.error("Enter the modified action before recording a Modified decision.")
                elif not reason.strip():
                    st.error("A reason is required before recording a decision.")
                else:
                    try:
                        save_decision(selected_id, actor.strip() or "Unknown planner", decision, reason.strip(), modified_action.strip(), expected_run_id=str(selected_incident.get("Run ID", "")))
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.success(f"Decision recorded: {decision}.")
                        st.rerun()
