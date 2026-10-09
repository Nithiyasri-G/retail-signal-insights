from html import escape
from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st

from market_intelligence.data.demo import demo_market_signals, demo_store_inventory, demo_store_master
from market_intelligence.demand.planning import (
    AVAILABLE_SUPPLY_ASSUMPTION,
    DEMAND_SIGNAL_SCOPE_FIELDS,
    GAP_DEFINITION,
    HISTORICAL_REFERENCE_NOTE,
    SURPLUS_DEFINITION,
    calculate_demand_plan,
    demand_plan_product_rows,
    demand_planning_editor_column_kinds,
    display_plan,
    forecast_input_label,
    planning_mode,
    signal_store_scope,
)
from market_intelligence.llm.audit import exportable_llm_audit
from market_intelligence.llm.demand_report import generate_demand_planner_report
from market_intelligence.signals.signal_log import build_demand_signal_log
from market_intelligence.ui.agents import DEMAND_AGENT, agent_credit, render_agent_text
from market_intelligence.ui.views.operational_impact.inbox import incident_table, kpi_row, kv_list
from market_intelligence.util.hashing import payload_hash
from market_intelligence.util.tables import format_timestamp

# Internal identifiers of a signal: they drive the calculation or the join to other pages and are
# not content a planner reads, so they are shown in Admin view only.
SIGNAL_INTERNAL_COLUMNS = ("Signal Key", "Top Event", "Catalog Categories")
SIGNAL_COLUMN_ORDER = ("Signal ID", "Level", "Signal Area", "Region", "Source", "Potential Business Effect", "Potential Forecast Input", "Business Impact", "Recommended Action")
SIGNAL_COLUMN_CONFIG = {
    "Signal ID": st.column_config.TextColumn(width=85),
    "Level": st.column_config.TextColumn(width=80),
    "Signal Area": st.column_config.TextColumn(width=120),
    "Region": st.column_config.TextColumn(width=70),
    "Source": st.column_config.TextColumn(width=160),
    "Potential Business Effect": st.column_config.TextColumn(width=280),
    "Potential Forecast Input": st.column_config.TextColumn(width=190),
    "Business Impact": st.column_config.TextColumn(width=330),
    "Recommended Action": st.column_config.TextColumn(width=330),
}
# The calculation reads four numbers plus the category; the rest of the inventory columns are
# catalogue or computed attributes (see demand_planning_editor_column_kinds).
INVENTORY_PLANNER_COLUMNS = ("Product", "Category", "Baseline Forecast", "Historical Average", "On Hand", "Inbound", "Case Pack", "MOQ")


def _section(label: str) -> None:
    st.markdown(f"<div class='section-title spaced'>{escape(label)}</div>", unsafe_allow_html=True)


CONTEXT_VALIDATION = {
    "cpi": "Review category sales, basket mix, customer traffic, pricing and promotions to determine whether inflation is changing customer behaviour.",
    "news": "Ask the category owner to confirm whether this event touches a specific category, product or geography before it is used in a planning decision.",
}


def _render_context_only(signal_row: Dict[str, Any], reason: str) -> None:
    """Lighter view for market-context signals: no store inventory calculation is run."""
    _section("2. Market context")
    area = str(signal_row.get("Signal Area", "") or "")
    kind = "news" if "news" in area.lower() else "cpi"
    st.info(reason)
    kv_list(
        [
            ("Signal", escape(f"{area} ({signal_row.get('Level', '')}) from {signal_row.get('Source', '')}")),
            ("Possible business impact", escape(str(signal_row.get("Business Impact", "") or "Not supplied"))),
            ("Planning review direction", "Review"),
            ("Category/geography mapping", "Not mapped to a product category" if kind == "cpi" else "Not mapped"),
            ("Recommended validation", escape(CONTEXT_VALIDATION[kind] if kind == "news" or "cpi" in area.lower() or "inflation" in area.lower() else str(signal_row.get("Recommended Action", "")))),
        ]
    )


def _related_incident_note(run: Dict[str, Any], signal_row: Dict[str, Any]) -> str:
    """How the selected signal lines up with incidents on the Operational Impact Center.

    The two pages answer different questions (state-level severity here, alerts that reach a store
    there), so a High weather signal for a state can sit beside incidents that never reach a store.
    Saying so removes the apparent contradiction.
    """
    incidents = (run or {}).get("operational_incidents_snapshot") or []
    area = str(signal_row.get("Signal Area", "")).lower()
    region = str(signal_row.get("Region", "") or "").strip().upper()
    if "weather" in area and len(region) == 2:
        related = [
            incident
            for incident in incidents
            if incident.get("Type") == "Weather"
            and str((incident.get("Scenario", {}) or {}).get("state", "")).upper() == region
        ]
        reaching = [incident for incident in related if incident.get("Store IDs")]
        return (
            f"Operational Impact Center: {len(related)} weather incident(s) for {region} in this run; "
            f"{len(reaching)} reach a store."
        )
    if "recall" in area:
        recalls = [incident for incident in incidents if incident.get("Type") == "Product Recall"]
        return f"Operational Impact Center: {len(recalls)} recall incident(s) in this run."
    return ""


def render_demand_planner_view(nvidia_key: str, nvidia_model: str) -> None:
    admin = bool(st.session_state.get("admin_view", False))
    st.markdown("<div class='page-head'><div class='page-title'>Demand Planner</div></div>", unsafe_allow_html=True)
    st.caption(
        "Does the current baseline forecast already anticipate a market signal? Pick a signal, check the store's position, then analyze.",
        help=(
            "This follows the Market Intelligence, Demand Signal Log, Demand Planning flow. It asks a different question from the "
            "Operational Impact Center: that view sizes the operational response to one specific alert inside its window, while this "
            "one asks whether the baseline forecast already anticipates the signal, across the product categories it implicates. "
            f"No manual scenario-adjustment percentage is applied; the {DEMAND_AGENT} only explains the calculated result."
        ),
    )

    run = st.session_state.get("run")
    run_feature_df = run.get("feature_df", pd.DataFrame()) if run else pd.DataFrame()
    signal_log = build_demand_signal_log(run_feature_df)
    using_demo_signals = signal_log.empty
    if using_demo_signals:
        signal_log = demo_market_signals()
        if run:
            st.caption("The latest run produced no signal rows, so reference signals are shown.")
        else:
            st.caption("Reference signals (no run in this session). Use Run intelligence in the left panel to use live signals.")
    else:
        st.caption(f"Signals from the run of {format_timestamp(run.get('timestamp', '')) or 'this session'} ({len(signal_log)} signals).")

    # ------------------------------------------------------------------ 1. signal
    _section("1. Market signal")
    # Scope fields drive the calculation but are not content: they showed as columns of "nan" and "None".
    hidden = set(DEMAND_SIGNAL_SCOPE_FIELDS) | (set() if admin else set(SIGNAL_INTERNAL_COLUMNS))
    # Display names: the signal describes a potential business effect and a potential (not yet
    # production) forecast input; the technical key stays in Admin view.
    signal_display = signal_log.rename(columns={"Demand Direction": "Potential Business Effect"})
    if "Forecast Feature" in signal_display.columns:
        signal_display["Potential Forecast Input"] = signal_display["Forecast Feature"].map(forecast_input_label)
        if admin:
            signal_display = signal_display.rename(columns={"Forecast Feature": "Forecast Feature (technical key)"})
        else:
            signal_display = signal_display.drop(columns=["Forecast Feature"])
    shown = [c for c in SIGNAL_COLUMN_ORDER if c in signal_display.columns]
    shown += [c for c in signal_display.columns if c not in shown and c not in hidden]
    clicked = incident_table(signal_display[shown].reset_index(drop=True), "dp_signal_table", SIGNAL_COLUMN_CONFIG, priority_col="Level")
    selected_position = clicked if clicked is not None and clicked < len(signal_log) else 0
    signal_row = signal_log.iloc[selected_position].to_dict()
    st.caption(
        f"Selected: {signal_row.get('Signal ID', '')} · {signal_row.get('Signal Area', '')}"
        + (" · click a row to choose another signal." if clicked is None else ".")
    )

    # Market-context signals (headline/fuel CPI, unmapped news) are not sized against store inventory.
    mode, mode_reason = planning_mode(signal_row)
    if mode == "context":
        st.session_state.pop("demand_plan_result", None)
        _render_context_only(signal_row, mode_reason)
        return

    # ------------------------------------------------------------------ 2. store
    _section("2. Store forecast and inventory")
    store_master = demo_store_master()

    # The store the planner picks here has to be reconcilable with the stores the Operational Impact
    # Center says the alert reached; stores a signal does not reach are marked, not hidden.
    exposed_store_ids, store_scope_basis = signal_store_scope(signal_row, store_master)
    store_options: Dict[str, str] = {}
    for _, row in store_master.iterrows():
        store_id = str(row["Store ID"])
        closed = str(row.get("Operating Status", "Open")) == "Closed"
        if closed and not admin:
            continue  # closed stores get no new planning calculation; historical review is Admin-only
        in_scope = not exposed_store_ids or store_id in exposed_store_ids
        suffix = "" if in_scope else "  (not exposed to this signal)"
        if closed:
            suffix = "  (Closed - historical review only)" + suffix
        store_options[f"{store_id} - {row['Store Name']}{suffix}"] = store_id

    # Default to a store the signal actually reaches, so the two views line up.
    default_index = 0
    if exposed_store_ids:
        for position, store_id in enumerate(store_options.values()):
            if store_id in exposed_store_ids:
                default_index = position
                break

    selected_store_label = st.selectbox(
        "Store (illustrative internal data)",
        list(store_options.keys()),
        index=default_index,
        help="Stores the selected signal does not reach are marked; you can still open one as baseline context.",
    )
    selected_store_id = store_options[selected_store_label]

    if exposed_store_ids and selected_store_id not in exposed_store_ids:
        st.warning(
            f"Store {selected_store_id} is outside this signal's geography. {store_scope_basis} "
            "The figures below are this store's baseline position, not its exposure to this signal, "
            "and they will not reconcile with the Operational Impact Center."
        )
    else:
        st.caption(store_scope_basis)
    related_note = _related_incident_note(run, signal_row)
    if related_note:
        st.caption(related_note)

    selected_store_row = store_master[store_master["Store ID"].astype(str) == selected_store_id].iloc[0]
    selected_store_name = str(selected_store_row["Store Name"])
    store_is_closed = str(selected_store_row.get("Operating Status", "Open")) == "Closed"
    if store_is_closed:
        st.warning("This store is closed. It is shown for historical review only; no planning calculation is produced for it.")

    # A new intelligence run must not inherit manual edits made against the previous one.
    run_marker = str(run.get("timestamp", "")) if run else "reference"
    new_run = st.session_state.get("demand_planning_run_marker") not in (None, run_marker)
    if st.session_state.get("demand_planning_store_id") != selected_store_id or new_run:
        st.session_state["demand_planning_data"] = demo_store_inventory(store_master)[
            lambda df: df["Store ID"] == selected_store_id
        ].drop(columns=["UPC"]).reset_index(drop=True)
        st.session_state["demand_planning_store_id"] = selected_store_id
        if new_run:
            st.session_state.pop("demand_planning_editor", None)
            st.caption("A new run completed, so the illustrative planning inputs were reset to source values.")
    st.session_state["demand_planning_run_marker"] = run_marker

    _editable_planning_data = st.session_state["demand_planning_data"]
    _planning_editor_column_config = {
        col: (
            st.column_config.NumberColumn(col, min_value=0)
            if kind == "numeric_input"
            else st.column_config.Column(col, disabled=True)
        )
        for col, kind in demand_planning_editor_column_kinds(list(_editable_planning_data.columns)).items()
    }
    # Historical average does not change the calculation, so it is a read-only reference.
    _planning_editor_column_config["Historical Average"] = st.column_config.NumberColumn("Historical Sales Reference", disabled=True)
    column_order = None if admin else [c for c in INVENTORY_PLANNER_COLUMNS if c in _editable_planning_data.columns]
    edited_df = st.data_editor(
        _editable_planning_data,
        width="stretch",
        hide_index=True,
        num_rows="fixed",
        key="demand_planning_editor",
        column_config=_planning_editor_column_config,
        column_order=column_order,
    )
    st.session_state["demand_planning_data"] = edited_df
    st.caption(
        "Edit forecast, on-hand or inbound to test a different position. " + HISTORICAL_REFERENCE_NOTE,
        help=(
            "Forecast, historical sales reference, on-hand and inbound inventory for the selected store (illustrative internal data). "
            "The market signal sets the planning direction; it does not automatically override the forecast."
        ),
    )

    input_fingerprint = payload_hash({"signal": signal_row, "store": selected_store_id,
        "data": edited_df.to_dict("records"), "run": run.get("timestamp") if run else "reference"})
    previous_result = st.session_state.get("demand_plan_result")
    if previous_result and previous_result.get("input_fingerprint") != input_fingerprint:
        st.session_state.pop("demand_plan_result", None)
        st.caption("Inputs changed. Analyze again to refresh the result for this signal and store.")
    if st.button("Analyze Demand Impact", type="primary", disabled=store_is_closed):
        plan = calculate_demand_plan(signal_row, edited_df, selected_store_name)
        if plan.get("Products In Scope", 0) == 0:
            report, report_source, report_audit = str(plan.get("Scope Basis", "")), "Local summary", {}
        else:
            with st.spinner(f"{DEMAND_AGENT} is working..."):
                report, report_source, report_audit = generate_demand_planner_report(
                    nvidia_key.strip(), nvidia_model.strip(), signal_row, plan
                )
        st.session_state["demand_plan_result"] = {
            "plan": plan,
            "report": report,
            "report_source": report_source,
            "report_audit": report_audit,
            "signal": signal_row,
            "using_demo_signals": using_demo_signals,
            "input_fingerprint": input_fingerprint,
            "product_rows": demand_plan_product_rows(
                signal_row, edited_df, selected_store_id, selected_store_name, str(run.get("timestamp", "")) if run else "reference"
            ),
        }

    result = st.session_state.get("demand_plan_result")
    if result:
        plan = result["plan"]
        _section("3. Result")
        st.caption(
            f"{plan.get('Planning Scope', '')}. Categories in scope: {plan.get('Category Scope', 'all')}.",
            help=str(plan.get("Scope Basis", "")),
        )
        if plan.get("Products In Scope", 0) == 0:
            st.warning(str(plan.get("Scope Basis", "")))
            return
        gap_value = int(plan.get("Baseline Gap", 0) or 0)
        kpi_row(
            [
                ("Planning review direction", str(plan["Demand Direction"]).capitalize(), ""),
                ("Products in scope", f"{plan.get('Products In Scope', 0)} of {plan.get('Products In Store', 0)}", ""),
                ("Baseline forecast", f"{plan.get('Baseline Forecast', 0):,} units", ""),
                (
                    "Current Baseline Supply Gap" if gap_value > 0 else "Product-level surplus",
                    f"{gap_value:,} units" if gap_value > 0 else f"{plan.get('Baseline Surplus', 0):,} units",
                    "bad" if gap_value > 0 else "good",
                ),
            ]
        )

        plan_rows: List[Tuple[str, str]] = [
            ("Signal", f"{plan.get('Signal Area', '')} ({plan.get('Signal Level', '')}) from {plan.get('Signal Source', '')}"),
            ("Scope", f"{plan.get('Products In Scope', 0)} of {plan.get('Products In Store', 0)} products: {plan.get('Category Scope', '')}"),
            ("Why this scope", str(plan.get("Scope Basis", ""))),
            ("Selected Store ID", str(plan.get("Selected Store ID", ""))),
            ("Selected Store", str(plan.get("Selected Store Name", ""))),
            ("Number of stores evaluated", str(plan.get("Stores Evaluated", 1))),
            ("Historical Sales Reference", f"{plan.get('Historical Average', 0):,} units"),
            ("Available supply", f"{plan.get('Available Supply', 0):,} units (on hand {plan.get('On Hand', 0):,} + inbound {plan.get('Inbound', 0):,})"),
            (
                "Current baseline supply gap" if plan.get("Baseline Gap", 0) > 0 else "Product-level surplus",
                f"{plan.get('Baseline Gap', 0):,} units short"
                if plan.get("Baseline Gap", 0) > 0
                else f"{plan.get('Baseline Surplus', 0):,} units surplus",
            ),
        ]
        kv_list([(label, escape(value)) for label, value in plan_rows])
        st.caption(GAP_DEFINITION)
        if plan.get("Baseline Gap", 0) > 0 and plan.get("Baseline Surplus", 0):
            st.caption(SURPLUS_DEFINITION.format(surplus=f"{plan['Baseline Surplus']:,}"))
        st.caption(AVAILABLE_SUPPLY_ASSUMPTION + " " + HISTORICAL_REFERENCE_NOTE)

        _section(f"4. {DEMAND_AGENT}")
        with st.container(border=True):
            render_agent_text(str(result["report"]))
            st.caption(agent_credit(DEMAND_AGENT, result["report_source"]))

        plan_df = pd.DataFrame(
            [("Planning review direction", str(plan.get("Demand Direction", "")))] + plan_rows + [
                ("Baseline forecast", f"{plan.get('Baseline Forecast', 0):,} units"),
            ],
            columns=["Planning Fact", "Value"],
        )
        export_plan = plan_df.to_csv(index=False).encode("utf-8")
        export_report = str(result["report"]).encode("utf-8")
        product_rows = result.get("product_rows")
        d1, d2, d3 = st.columns(3)
        with d1:
            st.download_button("Download demand_plan.csv", export_plan, "demand_plan.csv", "text/csv", width="stretch")
        with d2:
            if product_rows is not None:
                st.download_button(
                    "Download demand_plan_products.csv", product_rows.to_csv(index=False).encode("utf-8"),
                    "demand_plan_products.csv", "text/csv", width="stretch",
                )
        with d3:
            st.download_button("Download planning_report.txt", export_report, "planning_report.txt", "text/plain", width="stretch")

        with st.expander("Planning calculation audit", expanded=False):
            st.json(
                {
                    "Signal source": result["signal"],
                    "Calculated result": display_plan(plan),
                    "Report source": result["report_source"],
                    # The full audit carries the prompts and the request body sent to the agent;
                    # that is internal review material, gated on SHOW_LLM_TRACE.
                    "Report generation": exportable_llm_audit(result["report_audit"]),
                    "Important": (
                        "No manual scenario-adjustment percentage is applied. The external signal supplies planning context and direction; "
                        "a production forecast override requires validated internal historical demand data."
                    ),
                }
            )
