from market_intelligence.ui.operational_impact import history_selector_label
from market_intelligence.ui.operational_impact import weather_inbox_columns
from market_intelligence.weather.inbox import shorten
from typing import Any
from typing import Dict
from typing import List
import pandas as pd
import streamlit as st

from market_intelligence.persistence.operational_store import load_incident_payload, load_incidents
from market_intelligence.ui.components import style_operational_dataframe
from market_intelligence.ui.views.operational_impact.inbox import incident_filters, incident_table, live_feed_note, pin_selected
from market_intelligence.ui.views.operational_impact.incident_detail import render_incident_detail_panel
from market_intelligence.util.clock import utc_now
from market_intelligence.util.tables import drop_empty_columns
from market_intelligence.weather.inbox_helpers import enrich_weather_inbox, weather_inbox_export_csv


WEATHER_LABEL_COLUMNS = ("Weather Event", "Area of Impact", "Store Exposure", "Incident Reference")


def render_weather_incident_tab(incidents: List[Dict[str, Any]], nvidia_key: str, nvidia_model: str) -> None:

    weather_meta = load_incidents()
    weather_meta = weather_meta[(weather_meta["Type"] == "Weather") & (weather_meta["Demo"] == 0)].copy()

    # ------------------------------------------------------------------
    # Build the ACTIVE incident set explicitly:
    #   1) all live Weather incidents from the current run
    # Synthetic Weather scenarios are stored only in scenario_runs.
    # This avoids the controlled scenario disappearing because of old
    # history rows or filter state.
    # ------------------------------------------------------------------
    current_live_incidents = [
        inc for inc in incidents
        if inc.get("Type") == "Weather"
        and "Demonstration" not in str(inc.get("Source", ""))
    ]
    current_live_ids = {inc["Incident ID"] for inc in current_live_incidents}

    # The operational inbox is live-only. Synthetic scenarios have separate persistence.
    active_lookup = {
        inc["Incident ID"]: inc for inc in current_live_incidents
    }
    latest_demo_id = ""

    if not active_lookup:
        if st.session_state.get("run"):
            reason = live_feed_note(st.session_state.get("run"), "weather", "NOAA weather alerts")
            st.caption(f"No live weather incidents. {reason} {len(weather_meta)} earlier incident(s) are listed below.")
    else:
        # Use the stored metadata only as the base frame; if the just-created
        # demo is not yet represented there for any reason, synthesize one
        # lightweight row from its payload so it is still selectable.
        active_ids = list(active_lookup.keys())
        inbox_meta = weather_meta[weather_meta["Incident ID"].isin(active_ids)].copy()

        missing_meta_ids = [iid for iid in active_ids if iid not in set(inbox_meta["Incident ID"].astype(str))]
        for iid in missing_meta_ids:
            payload = active_lookup[iid]
            is_demo = 1 if payload.get("Evidence", {}).get("is_demo_scenario") or "Demonstration" in str(payload.get("Source", "")) else 0
            inbox_meta = pd.concat(
                [
                    inbox_meta,
                    pd.DataFrame(
                        [
                            {
                                "Incident ID": iid,
                                "Created At": utc_now(),
                                "Type": "Weather",
                                "Priority": payload.get("Priority", ""),
                                "Decision Status": payload.get("Decision Status", "Proposed"),
                                "Affected Scope": payload.get("Affected Scope", ""),
                                "State": payload.get("Scenario", {}).get("state", ""),
                                "Product": "",
                                "Event": payload.get("Event", ""),
                                "Store IDs": ", ".join(payload.get("Store IDs", []) or []),
                                "Run ID": payload.get("Run ID", ""),
                                "Demo": is_demo,
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )

        # Enrich only this small active set.
        inbox_df = enrich_weather_inbox(inbox_meta)

        if "State" not in inbox_df.columns:
            inbox_df["State"] = ""

        def _affected_store_text(row):
            store_ids = [x.strip() for x in str(row.get("Store IDs", "")).split(",") if x.strip()]
            if store_ids:
                return f"{len(store_ids)} ({', '.join(store_ids)})"
            scope = str(row.get("Affected Scope", "") or "")
            return scope if scope else "Not quantified"

        inbox_df["Affected Stores"] = inbox_df.apply(_affected_store_text, axis=1)

        # Most rows are expected to show "No Store match": the demo Store network only
        # covers 8 counties in TX and 8 in GA, so a live NOAA feed queried at the state
        # level (every active alert in TX/GA) will mostly cover counties with no demo
        # Store in them. That is a Store-network-coverage fact, not an app defect --
        # this line says so up front instead of leaving a wall of "Monitor" rows unexplained.
        matched_count = int(inbox_df["Operational Priority"].astype(str).str.strip().isin(["High", "Medium", "Low"]).sum())
        total_count = int(len(inbox_df))
        if total_count:
            st.caption(
                f"{matched_count} of {total_count} alert(s) reach a store in this network. The rest show "
                "'No Store match' because the demo network covers only 8 counties per state (TX/GA)."
            )

        filtered_df, export_col = incident_filters(
            inbox_df,
            "oic_weather",
            priority_col="Operational Priority",
            type_col="Weather Event",
            type_all="All event types",
            label_cols=WEATHER_LABEL_COLUMNS,
            extra=(("Actionability", "All actions"), ("Demand Basis", "All demand")),
            with_export_slot=True,
        )
        if filtered_df.empty:
            st.caption("No weather incidents match the filters.")
        else:
            priority_order = {"High": 0, "Medium": 1, "Low": 2}
            action_order = {"Act": 0, "Review": 1, "Monitor": 2}
            filtered_df = filtered_df.copy()
            filtered_df["_priority_sort"] = filtered_df["Operational Priority"].map(priority_order).fillna(9)
            filtered_df["_action_sort"] = filtered_df["Actionability"].map(action_order).fillna(9)
            filtered_df = filtered_df.sort_values(["_priority_sort", "_action_sort", "Created At"], ascending=[True, True, False])
            filtered_df = pin_selected(filtered_df, "oic_weather")

            visible_cols = drop_empty_columns(
                filtered_df, [c for c in weather_inbox_columns() if c in filtered_df.columns]
            )
            # Priority and the incident number first so both are on screen without scrolling.
            visible_cols = (
                [c for c in ("Operational Priority", "Incident Reference") if c in visible_cols]
                + [c for c in visible_cols if c not in ("Operational Priority", "Incident Reference")]
            )

            with export_col:
                st.download_button(
                    "Export CSV",
                    weather_inbox_export_csv(filtered_df, visible_cols),
                    "weather_inbox.csv",
                    "text/csv",
                    key="oic_weather_inbox_export",
                    width="stretch",
                )
            clicked = incident_table(
                filtered_df[visible_cols],
                "oic_weather_inbox_table",
                {
                    "Operational Priority": st.column_config.TextColumn("Priority", width=75),
                    "Weather Event": st.column_config.TextColumn(width=150),
                    "Area of Impact": st.column_config.TextColumn(width=190),
                    "Alert Window": st.column_config.TextColumn(width=190),
                    "Recommended Action": st.column_config.TextColumn(width="large"),
                    "Store Exposure": st.column_config.TextColumn(width=190),
                    "Store Match": st.column_config.TextColumn(width=140),
                    "Incident Reference": st.column_config.TextColumn(width=150),
                },
                priority_col="Operational Priority",
            )
            st.caption(
                "Store Match: whether the alert reaches a store. Demand Basis: whether uplift can be sized, "
                "from reviewed synthetic scenarios rather than observed client sales."
            )
            filtered_ids = filtered_df["Incident ID"].astype(str).tolist()
            selected_id = filtered_ids[clicked if clicked is not None and clicked < len(filtered_ids) else 0]
            if clicked is None:
                st.caption("Showing the highest-priority incident. Click a row to open another.")

            selected_incident = active_lookup.get(selected_id) or load_incident_payload(selected_id)
            selected_row = filtered_df[
                filtered_df["Incident ID"].astype(str) == str(selected_id)
            ]
            created_at = (
                selected_row.iloc[0]["Created At"]
                if not selected_row.empty and "Created At" in selected_row.columns
                else ""
            )

            # Short evidence note only for live incidents that cannot be quantified.
            if not selected_row.empty:
                assessment = str(selected_row.iloc[0].get("Assessment", "") or "")
                missing = str(selected_row.iloc[0].get("Missing Evidence", "") or "")
                # Read the live/demo flag from "Source Mode", which carries it, rather
                # than from the "Source" column that no longer exists on this table.
                source = str(selected_row.iloc[0].get("Source Mode", "") or "")

                if source == "Live NOAA" and assessment == "Insufficient Evidence":
                    st.warning(
                        "Live NOAA alert received, but Store-level impact is not quantified yet. "
                        f"Missing evidence: {missing}."
                    )
                elif source == "Live NOAA" and assessment == "No Internal Match":
                    st.info(
                        "Live NOAA alert received, but no matching internal operating footprint was found. "
                        f"Missing evidence: {missing}."
                    )

            # Preserve the complete existing Incident Detail exactly as before.
            render_incident_detail_panel(
                selected_incident,
                selected_id,
                created_at,
                nvidia_key,
                nvidia_model,
            )

    # ------------------------------------------------------------------
    # Earlier incidents: a second table, opened the same way (click a row).
    # ------------------------------------------------------------------
    history_df = weather_meta[
        ~weather_meta["Incident ID"].isin(current_live_ids)
    ].copy()

    if latest_demo_id:
        history_df = history_df[
            history_df["Incident ID"].astype(str) != str(latest_demo_id)
        ]

    if active_lookup:
        st.markdown('<div class="small-header">Earlier incidents</div>', unsafe_allow_html=True)

    if history_df.empty:
        st.caption("No earlier weather incidents are available yet.")
    else:
        history_display = history_df.sort_values("Created At", ascending=False).head(20)

        # Two Flood Watches for the same state showed as byte-identical rows differing
        # only by Incident ID -- the exact complaint the live inbox was fixed for. The
        # history table now carries the same differentiators: the alert's own geography
        # and window, which is what actually separates two alerts of one event type.
        history_display = enrich_weather_inbox(history_display)

        history_cols = [
            "Incident Reference",
            "Weather Event",
            "Area of Impact",
            "Alert Window",
            "Store Exposure",
            "Store Match",
            "Operational Priority",
            "Action Status",
            "Record Type",
            "Created At",
        ]
        history_cols = drop_empty_columns(history_display, history_cols)
        history_cols = (
            [c for c in ("Operational Priority", "Incident Reference") if c in history_cols]
            + [c for c in history_cols if c not in ("Operational Priority", "Incident Reference")]
        )
        history_display, _ = incident_filters(
            history_display,
            "oic_weather_history",
            priority_col="Operational Priority",
            type_col="Weather Event",
            type_all="All event types",
            label_cols=WEATHER_LABEL_COLUMNS,
        )

        history_click = incident_table(
            history_display[history_cols],
            "oic_weather_history_table",
            {
                "Operational Priority": st.column_config.TextColumn("Priority", width=75),
                "Weather Event": st.column_config.TextColumn(width=150),
                "Area of Impact": st.column_config.TextColumn(width=190),
                "Alert Window": st.column_config.TextColumn(width=190),
                "Store Exposure": st.column_config.TextColumn(width=190),
                    "Store Match": st.column_config.TextColumn(width=140),
                    "Incident Reference": st.column_config.TextColumn(width=150),
            },
            priority_col="Operational Priority",
        )
        history_ids = history_display["Incident ID"].astype(str).tolist()
        if history_click is None or history_click >= len(history_ids):
            st.caption("Click a row to open its detail.")
        else:
            selected_history_id = history_ids[history_click]
            selected_history_created_at = str(history_display.iloc[history_click].get("Created At", "") or "")
            render_incident_detail_panel(
                load_incident_payload(str(selected_history_id)),
                str(selected_history_id),
                selected_history_created_at,
                nvidia_key,
                nvidia_model,
                source_label="Historical Incident",
            )

        if len(history_df) > 20:
            st.caption(
                f"Showing the 20 most recent of {len(history_df)} earlier weather incidents."
            )