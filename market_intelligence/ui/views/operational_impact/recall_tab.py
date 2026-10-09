from market_intelligence.weather.inbox import shorten
from typing import Any
from typing import Dict
from typing import List
import pandas as pd
import streamlit as st

from market_intelligence.data.distribution import distribution_summary
from market_intelligence.persistence.operational_store import load_incident_payload, load_incidents
from market_intelligence.ui.components import style_operational_dataframe
from market_intelligence.ui.views.operational_impact.inbox import incident_filters, incident_table, live_feed_note
from market_intelligence.ui.views.operational_impact.incident_detail import render_incident_detail_panel
from market_intelligence.util.clock import utc_now
from market_intelligence.util.tables import drop_empty_columns, format_recall_date
from market_intelligence.weather.inbox_helpers import enrich_recall_history




def render_recall_incident_tab(incidents: List[Dict[str, Any]], nvidia_key: str, nvidia_model: str) -> None:

    recall_meta = load_incidents()
    recall_meta = recall_meta[(recall_meta["Type"] == "Product Recall") & (recall_meta["Demo"] == 0)].copy()

    # ------------------------------------------------------------------
    # ACTIVE Recall incidents:
    #   1) all live openFDA incidents from the current run
    # Synthetic Recall scenarios are stored only in scenario_runs.
    # ------------------------------------------------------------------
    current_live_incidents = [
        inc for inc in incidents
        if inc.get("Type") == "Product Recall"
        and "Demonstration" not in str(inc.get("Source", ""))
    ]
    current_live_ids = {inc["Incident ID"] for inc in current_live_incidents}

    active_lookup = {
        inc["Incident ID"]: inc for inc in current_live_incidents
    }
    latest_demo_id = ""

    if not active_lookup:
        if st.session_state.get("run"):
            reason = live_feed_note(st.session_state.get("run"), "fda", "openFDA recalls")
            st.caption(f"No live recall incidents. {reason} {len(recall_meta)} earlier incident(s) are listed below.")
    else:
        active_ids = list(active_lookup.keys())
        inbox_meta = recall_meta[
            recall_meta["Incident ID"].isin(active_ids)
        ].copy()

        # If a newly-created controlled demo is not yet represented in metadata
        # for any reason, synthesize the lightweight row so it remains selectable.
        existing_meta_ids = set(inbox_meta["Incident ID"].astype(str).tolist())
        missing_meta_ids = [iid for iid in active_ids if iid not in existing_meta_ids]

        for iid in missing_meta_ids:
            payload = active_lookup[iid]
            is_demo = 1 if (
                payload.get("Evidence", {}).get("is_demo_scenario")
                or "Demonstration" in str(payload.get("Source", ""))
            ) else 0

            inbox_meta = pd.concat(
                [
                    inbox_meta,
                    pd.DataFrame(
                        [
                            {
                                "Incident ID": iid,
                                "Created At": utc_now(),
                                "Type": "Product Recall",
                                "Priority": payload.get("Priority", ""),
                                "Decision Status": payload.get("Decision Status", "Proposed"),
                                "Affected Scope": payload.get("Affected Scope", ""),
                                "State": "",
                                "Product": payload.get("Match", {}).get("matched_product", ""),
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

        # Enrich only the small active inbox.
        inbox_rows = []
        for _, row in inbox_meta.iterrows():
            record = row.to_dict()
            incident_id = str(record.get("Incident ID", ""))
            payload = active_lookup.get(incident_id) or load_incident_payload(incident_id) or {}

            evidence = payload.get("Evidence", {}) or {}
            match = payload.get("Match", {}) or {}

            source_mode = (
                "Controlled Demo"
                if int(record.get("Demo", 0) or 0) == 1
                else "Live openFDA"
            )

            # "Category review: Food / Consumables" is our internal match label, not a
            # product; under a "Recall Product" heading it reads as though openFDA
            # recalled a category. The real recalled product goes in this column and the
            # match label stays in "Match Level" where it belongs.
            matched_product = str(match.get("matched_product") or "")
            if matched_product.startswith(("Category review", "Distribution review")):
                matched_product = ""
            recalled_product = shorten(
                str(
                    matched_product
                    or evidence.get("product")
                    or evidence.get("product_description")
                    or record.get("Product")
                    or "Product not identified"
                ),
                90,
            )

            classification = (
                evidence.get("classification")
                or payload.get("Event")
                or "Recall"
            )

            match_status = str(
                payload.get("Status")
                or match.get("match_status")
                or ""
            )

            match_label = (
                payload.get("Match Type")
                or match.get("match_type")
                or {
                    "confirmed_exact": "Exact UPC/Lot Match",
                    "confirmed_upc_only": "UPC Match",
                    "probable_text_match": "Product/Vendor Match",
                    "category_hazard_match": "Category/Hazard Match",
                    "distribution_match": "Distribution Match",
                    "unmatched": "No Internal Match",
                }.get(
                    match_status,
                    match_status.replace("_", " ").title() or "Not Assessed"
                )
            )

            store_ids = payload.get("Store IDs", []) or []
            # Two different questions, kept apart: stores inside the recall's distribution states
            # are only candidates; "confirmed" needs an exact UPC and lot match.
            if not match.get("distribution_known", False):
                footprint_stores = "Unknown distribution"
            else:
                footprint_stores = str(int(match.get("footprint_store_count", 0) or 0))
            if match_status == "product_review_required" and match.get("candidate_estimate_available"):
                candidate_stores = str(int(match.get("candidate_stores_carrying", 0) or 0))
            else:
                candidate_stores = "Not assessed"
            if store_ids:
                confirmed_stores = f"{len(store_ids)} ({', '.join(map(str, store_ids))})"
            elif match_status == "lot_review_required":
                confirmed_stores = "Pending lot confirmation"
            else:
                confirmed_stores = "0"

            # Five Peanut Butter recalls in one run differed only by Incident ID, with
            # identical product, match reason and store list -- the same complaint raised
            # about repeated weather rows. openFDA already gives us what separates them.
            record.update(
                {
                    "Source": source_mode,
                    "Recall Product": str(recalled_product),
                    "Classification": str(classification),
                    "FDA Recalling Firm": str(evidence.get("recalling_firm", "") or "Not supplied"),
                    "Recall Number": str(evidence.get("recall_number", "") or evidence.get("event_id", "") or "Not supplied"),
                    "Recall Reason": shorten(str(evidence.get("reason", "") or "Not supplied"), 110),
                    "Recall Date": format_recall_date(evidence.get("recall_date")),
                    "Distribution": distribution_summary(evidence.get("distribution_pattern", "")),
                    "Recall Status": str(evidence.get("status", "") or "Not supplied"),
                    "Match Level": match_label,
                    "Match Reason": str(payload.get("Match Reason") or match.get("match_reason") or match.get("reason") or ""),
                    "Distribution-Footprint Stores": footprint_stores,
                    "Stores Carrying Possible Product Match": candidate_stores,
                    "UPC/Lot-Confirmed Affected Stores": confirmed_stores,
                }
            )
            inbox_rows.append(record)

        inbox_df = pd.DataFrame(inbox_rows)

        filtered_df, _ = incident_filters(
            inbox_df,
            "oic_recall",
            priority_col="Priority",
            type_col="Classification",
            type_all="All classes",
            label_cols=("Recall Product", "FDA Recalling Firm", "Classification", "Recall Number", "Incident ID"),
            extra=(("Source", "All sources"),),
        )
        if filtered_df.empty:
            st.caption("No recall incidents match the filters.")
        else:
            # Keep the Recall inbox concise and decision-oriented.
            recall_visible_cols = [
                # Identity -- which recall is this, and who issued it.
                "Priority",
                "Incident ID",
                "Recall Number",
                "Source",
                "Recall Product",
                "Classification",
                # Exposure -- does it reach us, and how firmly.
                "Distribution",
                "Match Level",
                "Distribution-Footprint Stores",
                "Stores Carrying Possible Product Match",
                "UPC/Lot-Confirmed Affected Stores",
                # Decision.
                "Decision Status",
                "Recall Reason",
            ]
            recall_visible_cols = drop_empty_columns(
                filtered_df, [c for c in recall_visible_cols if c in filtered_df.columns]
            )
            # Priority and the incident number first so both are on screen without scrolling.
            recall_visible_cols = (
                [c for c in ("Priority", "Incident ID", "Recall Number") if c in recall_visible_cols]
                + [c for c in recall_visible_cols if c not in ("Priority", "Incident ID", "Recall Number")]
            )

            st.caption(
                "Distribution-Footprint Stores are candidates inside the recall's distribution states, not confirmed affected "
                "stores. Stores Carrying Possible Product Match applies to probable product matches only (internal assortment is synthetic). "
                "UPC/Lot-Confirmed Affected Stores needs an exact UPC and lot match."
            )
            clicked = incident_table(
                filtered_df[recall_visible_cols],
                "oic_recall_inbox_table",
                {
                    "Priority": st.column_config.TextColumn(width=75),
                    "Recall Product": st.column_config.TextColumn(width=200),
                    "Recall Reason": st.column_config.TextColumn(width="large"),
                    "Incident ID": st.column_config.TextColumn(width=150),
                },
                priority_col="Priority",
            )
            filtered_ids = filtered_df["Incident ID"].astype(str).tolist()
            selected_id = filtered_ids[clicked if clicked is not None and clicked < len(filtered_ids) else 0]
            if clicked is None:
                st.caption("Showing the highest-priority incident. Click a row to open another.")

            selected_row = filtered_df[
                filtered_df["Incident ID"].astype(str) == str(selected_id)
            ]
            selected_incident = (
                active_lookup.get(selected_id)
                or load_incident_payload(selected_id)
            )

            # Short explanation for live FDA recalls that are not yet confirmed
            # against internal product / UPC / lot data.
            if not selected_row.empty:
                source = str(selected_row.iloc[0].get("Source", "") or "")
                internal_match = str(selected_row.iloc[0].get("Match Level", "") or "")
                recalled_product = str(selected_row.iloc[0].get("Recall Product", "") or "")

                if source == "Live openFDA" and internal_match == "No Internal Match":
                    st.info(
                        f"Live openFDA recall received for {recalled_product}, but it could not be matched "
                        "confidently to the demonstration product/UPC data. Store, DC and financial exposure "
                        "are therefore not quantified."
                    )
                elif source == "Live openFDA" and internal_match in {"Product/Vendor Match", "Probable Match", "Probable product text match"}:
                    st.warning(
                        f"Live openFDA recall received for {recalled_product}. A probable product match was identified. "
                        "The displayed Store/DC quantities are planning estimates only; confirm the UPC and lot "
                        "before treating them as affected inventory or initiating action."
                    )
                elif source == "Live openFDA" and internal_match in {"Category/Hazard Match", "Distribution Match"}:
                    st.info(
                        f"Live openFDA recall received for {recalled_product}. The evidence ladder found a {internal_match.lower()}, "
                        "so the incident is actionable for review but Store/DC exposure is not quantified until UPC/product confirmation."
                    )

            created_at = (
                selected_row.iloc[0]["Created At"]
                if not selected_row.empty and "Created At" in selected_row.columns
                else ""
            )

            # Preserve the complete existing Recall detail:
            # Impact | Evidence | Recommended Actions | Decision History.
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
    history_df = recall_meta[
        ~recall_meta["Incident ID"].isin(current_live_ids)
    ].copy()

    if latest_demo_id:
        history_df = history_df[
            history_df["Incident ID"].astype(str) != str(latest_demo_id)
        ]

    if active_lookup:
        st.markdown('<div class="small-header">Earlier incidents</div>', unsafe_allow_html=True)

    if history_df.empty:
        st.caption("No earlier recall incidents are available yet.")
    else:
        history_display = history_df.sort_values("Created At", ascending=False).head(20)

        # Five recalls of one product showed as identical history rows differing only by
        # Incident ID -- the same defect fixed in the live inbox and the weather history.
        history_display = enrich_recall_history(history_display).rename(columns={"Recalling Firm": "FDA Recalling Firm"})

        history_cols = [
            "Incident ID",
            "Recall Number",
            "Recall Date",
            "Product",
            "FDA Recalling Firm",
            "Classification",
            "Recall Reason",
            "Affected Scope",
            "Priority",
            "Decision Status",
        ]
        history_cols = drop_empty_columns(history_display, history_cols)
        history_cols = (
            [c for c in ("Priority", "Incident ID", "Recall Number") if c in history_cols]
            + [c for c in history_cols if c not in ("Priority", "Incident ID", "Recall Number")]
        )
        history_display, _ = incident_filters(
            history_display,
            "oic_recall_history",
            priority_col="Priority",
            type_col="Classification",
            type_all="All classes",
            label_cols=("Product", "FDA Recalling Firm", "Classification", "Recall Number", "Incident ID"),
        )


        history_click = incident_table(
            history_display[history_cols],
            "oic_recall_history_table",
            {
                "Priority": st.column_config.TextColumn(width=75),
                "Product": st.column_config.TextColumn(width=200),
                "FDA Recalling Firm": st.column_config.TextColumn(width=190),
                "Recall Reason": st.column_config.TextColumn(width="large"),
                "Incident ID": st.column_config.TextColumn(width=150),
            },
            priority_col="Priority",
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
                f"Showing the 20 most recent of {len(history_df)} earlier recall incidents."
            )