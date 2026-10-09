from html import escape
from market_intelligence.scenarios.service import ScenarioRecord
from market_intelligence.scenarios.service import list_scenarios
from market_intelligence.scenarios.service import save_scenario
from market_intelligence.ui.scenario_lab import scenario_banner
from typing import Any
from typing import Dict
from typing import List
import json
import pandas as pd
import streamlit as st

from market_intelligence.data.demo import demo_product_catalog, demo_recall_catalog
from market_intelligence.data.demo_scenarios import DEMO_SCENARIO_STATES, DEMO_SCENARIO_WEATHER_EVENT_TYPES, demo_recall_probable_scenario, demo_recall_scenario, demo_weather_alert_scenario
from market_intelligence.incidents.builders import build_recall_incidents, build_weather_incident, build_weather_incidents
from market_intelligence.persistence.operational_store import _json_safe_incident, load_incidents, save_incident
from market_intelligence.ui.components import render_metric_card
from market_intelligence.ui.views.operational_impact.incident_detail import render_incident_detail_panel
from market_intelligence.ui.views.operational_impact.recall_tab import render_recall_incident_tab
from market_intelligence.ui.views.operational_impact.weather_tab import render_weather_incident_tab
from market_intelligence.util.clock import utc_now
from market_intelligence.util.tables import format_timestamp

from market_intelligence.config import settings
from market_intelligence.config.flags import scenario_lab_enabled

def render_operational_impact_center(nvidia_key: str, nvidia_model: str) -> None:
    run = st.session_state.get("run")
    results = run.get("results", {}) if run else {}
    weather_result = results.get("weather")
    fda_result = results.get("fda")
    run_timestamp = run.get("timestamp", "") if run else ""
    incidents: List[Dict[str, Any]] = []
    unmatched_count = 0
    if run and "operational_incidents_snapshot" in run:
        incidents = run["operational_incidents_snapshot"]
    else:
        if weather_result and weather_result.get("status") == "success":
            incidents.extend(build_weather_incidents(weather_result, run_timestamp))
        if fda_result and fda_result.get("status") == "success":
            incidents.extend(build_recall_incidents(fda_result, run_timestamp))
        incidents = list({inc["Incident ID"]: inc for inc in incidents}.values())
        if run:
            run["operational_incidents_snapshot"] = incidents
        for incident in incidents:
            save_incident(incident)
    unmatched_count = sum(inc["Status"] in ("no_match", "insufficient_evidence", "unmatched") for inc in incidents)
    all_incidents = load_incidents()

    def _source_dot(result: Any, name: str) -> str:
        status = str(result.get("status", "Not run")) if result else "Not run"
        state = "on" if status == "success" else "bad" if status == "failed" else "off"
        label = status.replace("_", " ").capitalize()
        return f"<span title='{escape(name)}: {escape(label)}'><span class='status-dot {state}'></span>{escape(name)}</span>"

    st.markdown(
        "<div class='page-head'><div class='page-title'>Operational Impact Center</div>"
        f"<div class='page-meta'>{_source_dot(weather_result, 'NOAA weather')}{_source_dot(fda_result, 'openFDA recalls')}</div></div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Live NOAA and openFDA alerts assessed against the store and distribution-centre network. "
        "Internal operations data is illustrative.",
        help=(
            "Each distinct NOAA weather alert or openFDA recall is treated as one incident and evaluated against a "
            "connected illustrative internal dataset (stores, DCs, routes, products, suppliers, inventory, sales, "
            "staffing; all synthetic and clearly labelled). Stores, products, routes and staffing are impact details "
            "inside that incident. No match and insufficient evidence are assessment outcomes, not additional "
            "incidents. Deterministic logic establishes the facts; the agents only explain them."
        ),
    )
    if not run:
        st.caption("No run in this session yet. Use Run intelligence in the left panel to refresh; earlier incidents are shown below.")

    def _tab_label(name: str, incident_type: str) -> str:
        """"Weather (6 live)" when the latest run produced incidents, else "Weather (16 earlier)".

        One bare number used to mean two different things: this run's incidents once a run existed,
        and every stored incident before that.
        """
        live = [
            inc for inc in incidents
            if inc.get("Type") == incident_type and "Demonstration" not in str(inc.get("Source", ""))
        ]
        if live:
            return f"{name} ({len(live)} live)"
        earlier = int(((all_incidents["Type"] == incident_type) & (all_incidents["Demo"] == 0)).sum())
        return f"{name} ({earlier} earlier)"

    tab_labels = [_tab_label("Weather", "Weather"), _tab_label("Product recall", "Product Recall")]
    if scenario_lab_enabled():
        tab_labels.append("What-if scenario")
    tabs = st.tabs(tab_labels)
    with tabs[0]:
        render_weather_incident_tab(incidents, nvidia_key, nvidia_model)
    with tabs[1]:
        render_recall_incident_tab(incidents, nvidia_key, nvidia_model)
    if scenario_lab_enabled():
        with tabs[2]:
            render_scenario_lab()


DEFAULT_CREATOR = "POC User"
RECALL_SCENARIO_LEVELS = ["Exact UPC and lot", "Probable product match (title only)"]
SCENARIO_ONLY_LABEL = "Demonstration only \u2014 no operational execution"


def scenario_header_rows(record: ScenarioRecord) -> List[tuple]:
    """The inputs the user chose, repeated at the top of the detail so the result is never ambiguous."""
    rows: List[tuple] = [("Scenario ID", record.scenario_id), ("Type", record.scenario_type)]
    assumptions = record.assumptions or {}
    if record.scenario_type == "Weather":
        rows += [("Event", assumptions.get("event", "")), ("State", assumptions.get("state", ""))]
    else:
        rows.append(("Product", assumptions.get("product", "")))
        if assumptions.get("match_level"):
            rows.append(("Match level", assumptions["match_level"]))
    rows += [
        ("Created by", record.created_by),
        ("Created at", format_timestamp(record.created_at)),
        ("Assumption version", record.assumption_version),
    ]
    return rows


def _render_scenario_header(record: ScenarioRecord) -> None:
    cells = "".join(
        f"<div class='stat-cell'><div class='stat-label'>{escape(label)}</div>"
        f"<div class='stat-value'>{escape(str(value))}</div></div>"
        for label, value in scenario_header_rows(record)
    )
    st.markdown(f"<div class='stat-row'>{cells}</div>", unsafe_allow_html=True)
    st.caption(SCENARIO_ONLY_LABEL)


def _render_scenario_detail(scenario_type: str, selector_key: str, empty_text: str) -> None:
    """A selector limited to one scenario type (or all) and the detail of whichever one is open."""
    records = [r for r in list_scenarios(settings.RUN_HISTORY_DB) if scenario_type in ("", r.scenario_type)]
    if not records:
        st.caption(empty_text)
        return
    lookup = {record.scenario_id: record for record in records}
    if st.session_state.get(selector_key) not in lookup:
        st.session_state.pop(selector_key, None)
    selection = st.selectbox(
        "Open scenario detail",
        list(lookup),
        key=selector_key,
        format_func=lambda sid: f"{sid} \u00b7 {lookup[sid].scenario_type} \u00b7 {format_timestamp(lookup[sid].created_at)}",
    )
    record = lookup[selection]
    _render_scenario_header(record)
    incident = json.loads(json.dumps(record.result, default=str))
    scenario = incident.get("Scenario", {})
    for key in ("scenario_df", "staffing_df", "employee_detail_df", "traffic_df"):
        if isinstance(scenario.get(key), list):
            scenario[key] = pd.DataFrame(scenario[key])
    render_incident_detail_panel(
        incident, record.scenario_id, record.created_at, "", "", scenario_view=True, key_prefix=f"{selector_key}_"
    )


def render_scenario_lab() -> None:
    """Build a synthetic what-if incident to walk through the workflow; stored apart from live incidents."""
    st.markdown(f"<div class='notice-bar'>{escape(scenario_banner())}</div>", unsafe_allow_html=True)
    st.caption(
        "Build a what-if scenario to walk through the workflow end to end. Scenarios are stored separately "
        "and never enter the live inbox.",
        help="Scenarios use versioned assumptions and cannot record live approvals.",
    )
    created_by = st.text_input("Created by", value=DEFAULT_CREATOR, key="scenario_created_by").strip()
    if not created_by:
        st.caption("Enter a name in Created by to create a scenario.")
    weather_tab, recall_tab, history_tab = st.tabs(["Weather scenario", "Recall scenario", "Scenario history"])

    with weather_tab:
        demo_event = st.selectbox("Event type", DEMO_SCENARIO_WEATHER_EVENT_TYPES, key="lab_weather_event")
        demo_state = st.selectbox("State", DEMO_SCENARIO_STATES, key="lab_weather_state")
        if st.button("Create weather scenario", key="lab_create_weather", disabled=not created_by):
            weather_result = demo_weather_alert_scenario(demo_event, demo_state)
            incident = build_weather_incident(weather_result, f"scenario-{utc_now()}")
            record = ScenarioRecord.create(
                scenario_type="Weather",
                created_by=created_by,
                assumptions={"event": demo_event, "state": demo_state},
                result=_json_safe_incident(incident),
            )
            save_scenario(settings.RUN_HISTORY_DB, record)
            # Open the scenario just created, not whichever one was open before.
            st.session_state["lab_detail_weather"] = record.scenario_id
            st.session_state["lab_detail_history"] = record.scenario_id
            st.success(f"Scenario {record.scenario_id} saved to the what-if scenario history.")
        _render_scenario_detail("Weather", "lab_detail_weather", "No weather scenarios have been created yet.")

    with recall_tab:
        match_level = st.selectbox("Match level", RECALL_SCENARIO_LEVELS, key="lab_recall_level")
        probable = match_level == RECALL_SCENARIO_LEVELS[1]
        if probable:
            products = demo_recall_catalog()["Product"].tolist()
        else:
            catalog = demo_product_catalog()
            products = catalog[catalog["Lot Tracked"] == True]["Product"].tolist()  # noqa: E712
        demo_product = st.selectbox("Product", products, key="lab_recall_product_probable" if probable else "lab_recall_product")
        if st.button("Create recall scenario", key="lab_create_recall", disabled=not created_by):
            item = demo_recall_probable_scenario(demo_product) if probable else demo_recall_scenario(demo_product)
            built = build_recall_incidents({"status": "success", "items": [item]}, f"scenario-{utc_now()}")
            result = _json_safe_incident(built[0]) if built else {"status": "not_created"}
            record = ScenarioRecord.create(
                scenario_type="Product Recall",
                created_by=created_by,
                assumptions={"product": demo_product, "match_level": match_level},
                result=result,
            )
            save_scenario(settings.RUN_HISTORY_DB, record)
            st.session_state["lab_detail_recall"] = record.scenario_id
            st.session_state["lab_detail_history"] = record.scenario_id
            st.success(f"Scenario {record.scenario_id} saved to the what-if scenario history.")
        _render_scenario_detail("Product Recall", "lab_detail_recall", "No recall scenarios have been created yet.")

    with history_tab:
        scenario_rows = [
            {
                "Scenario ID": record.scenario_id,
                "Type": record.scenario_type,
                "Created By": record.created_by,
                "Created At": format_timestamp(record.created_at),
                "Assumptions": ", ".join(
                    f"{str(key).replace('_', ' ').title()}: {value}"
                    for key, value in sorted(record.assumptions.items())
                ),
                "Result Status": str(
                    record.result.get("Status", record.result.get("status", ""))
                ).replace("_", " ").title(),
            }
            for record in list_scenarios(settings.RUN_HISTORY_DB)
        ]
        if scenario_rows:
            st.dataframe(pd.DataFrame(scenario_rows), width="stretch", hide_index=True)
        else:
            st.info("No scenarios have been created yet.")
        _render_scenario_detail("", "lab_detail_history", "")
