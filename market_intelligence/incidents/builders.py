from market_intelligence.provenance.models import DataProvenance
from typing import Any
from typing import Dict
from typing import List
import hashlib
import pandas as pd

from market_intelligence.incidents.actions import recall_recommended_actions, weather_recommended_actions
from market_intelligence.recalls.ladder import ladder_entry
from market_intelligence.recalls.matching import candidate_store_statement
from market_intelligence.recalls.parsing import normalize_recall_classification
from market_intelligence.util.clock import utc_now
from market_intelligence.weather.assumptions import store_match_phrase, weather_affected_scope
from market_intelligence.weather.planning import _traffic_pct_and_multiplier
from market_intelligence.weather.scenario import build_weather_scenario

from market_intelligence.recalls import matching as recall_matching

def build_weather_incident(weather_result: Dict[str, Any], run_timestamp: str = "") -> Dict[str, Any]:
    scenario_result = build_weather_scenario(weather_result)
    rows = weather_result.get("rows") or []
    items = weather_result.get("items") or []
    signal = rows[0] if rows else {}
    item = items[0] if items else {}
    is_demo = bool(signal.get("is_demo_scenario") or item.get("is_demo_scenario") or "Demonstration" in str(signal.get("source", "")))
    source_key = str(item.get("alert_id") or "").strip()
    if not source_key:
        source_key = "|".join(
            [str(item.get("event", "")), str(item.get("area_desc", "")), str(item.get("onset") or item.get("effective", "")), str(signal.get("region", ""))]
        )
    id_material = (f"demo|{source_key}" if is_demo else f"live|{source_key}")
    # 12 hex chars (48 bits) rather than 8 (32 bits): save_incident's ON CONFLICT would
    # silently MERGE two different alerts that happened to collide on this ID, which at
    # 32 bits has a non-trivial collision probability by a few thousand incidents. 48
    # bits pushes that into the "won't happen in this tool's lifetime" range.
    incident_id = f"WX-{hashlib.md5(id_material.encode()).hexdigest()[:12].upper()}"
    status = scenario_result["status"]
    if scenario_result.get("match_type"):
        match_type = str(scenario_result.get("match_type"))
    elif status == "ok":
        match_type = "Exact Scenario Match"
    elif status == "no_match":
        match_type = "Not assessed - no Store match"
    else:
        match_type = "Evidence Limited"
    match_reason = str(scenario_result.get("match_reason") or scenario_result.get("reason") or "")
    affected_stores = scenario_result.get("affected_stores", []) or []
    store_ids = [str(s.get("Store ID", "")) for s in affected_stores if str(s.get("Store ID", ""))]

    # Review requirement (No Store Intersection): retain the external alert, but leave
    # Operational Priority blank -- not "Low"/"Medium" -- when no Store actually matched,
    # and say plainly that no Store action applies rather than implying a graded response.
    no_store_action = [
        {
            "Action": "No Store action applies",
            "Owner": "Demand Planning",
            "Urgency": "None",
            "Reason": scenario_result.get("reason", "No internal Store falls within this alert's geography."),
            "Evidence": "Geography match evaluated against internal Store master; no Store intersects the alert.",
            "Decision Support": "Retain the alert for visibility only; no replenishment, staffing, or route action is required.",
            "Status": "No action required",
        }
    ]
    if status == "no_match":
        priority, impact_metrics, actions = "", {}, no_store_action
        limitations = [scenario_result.get("reason", "")]
        affected_scope = "No internal match"
    elif status == "insufficient_evidence":
        impact_metrics = {
            "Comparable scenario cases": int(scenario_result.get("comparable_events", 0) or 0),
            "Evidence ladder": match_type,
        }
        if scenario_result.get("category_hints"):
            impact_metrics["Sensitive categories"] = ", ".join(scenario_result.get("category_hints", [])[:4])
        limitations = [scenario_result.get("reason", "")]
        precision = scenario_result.get("match_precision", "")
        closed_in_footprint = scenario_result.get("closed_stores_in_footprint") or []
        if scenario_result.get("evidence_code") == "ALERT_EXPIRED":
            # Distinct from "no Store matched": Store(s) DID match geographically, the
            # alert simply ended before assessment. Not blank-priority (a real alert with
            # a real Store match happened), but not an executable Medium either -- a
            # monitor-only outcome with its own explicit action, matching the pattern
            # used for closed-Store/no-match cases.
            priority = "Low"
            actions = [
                {
                    "Action": "Monitor only - alert window has ended",
                    "Owner": "Demand Planning",
                    "Urgency": "None",
                    "Reason": scenario_result.get("reason", "This alert's end time has passed."),
                    "Evidence": "Alert lifecycle evaluated as Expired (end time is before the assessment time).",
                    "Decision Support": "No new replenishment plan can be executed against an ended alert window; monitor for a follow-on or updated alert.",
                    "Status": "No action required",
                }
            ]
            affected_scope = f"{scenario_result.get('state', '')} -- alert window ended; monitor only"
        elif store_ids:
            priority, actions = "Medium", []
            affected_scope = weather_affected_scope(len(store_ids), scenario_result.get("state", ""), precision)
        elif closed_in_footprint:
            # Distinct from "no Store matched": a Store DID match geographically, but is
            # Closed, so no operational action applies -- do not say "no Store matched"
            # when one plainly did.
            priority, actions = "", no_store_action
            closed_ids = ", ".join(str(s.get("Store ID", "")) for s in closed_in_footprint)
            affected_scope = (
                f"{scenario_result.get('state', '')} -- matched Store(s) Closed "
                f"({len(closed_in_footprint)}: {closed_ids}); no action applies"
            )
        else:
            priority, actions = "", no_store_action
            evaluated_count = len(
                scenario_result.get("evaluated_stores", [])
                or scenario_result.get("candidate_stores", [])
                or []
            )
            affected_scope = (
                f"{scenario_result.get('state', '')} -- no Store matched; "
                f"{evaluated_count} Store{'' if evaluated_count == 1 else 's'} evaluated "
                f"({store_match_phrase(precision)})"
            )
    else:
        df = scenario_result["scenario_df"]
        staffing_df = scenario_result.get("staffing_df", pd.DataFrame())
        gap_rows = df[df["Inventory Gap"] > 0]
        high_staffing = (staffing_df["Staffing Risk"] == "High").any() if not staffing_df.empty else False
        priority = "High" if (df["Route Risk"] == "Yes").any() or high_staffing else ("Medium" if not gap_rows.empty else "Low")
        # Itemized (not one fused sentence): each is a distinct caveat a planner may
        # care about independently, and joining them into one run-on sentence made all
        # four read as one undifferentiated disclaimer.
        limitations = [
            "Internal operations (Store/DC/inventory/route/staffing data) are synthetic fixtures, not live production data.",
            f"Store mapping method: {scenario_result.get('match_precision', 'not evaluated')}.",
            "ATP is reserved once per DC/UPC across this run; availability shown is not a live stock promise.",
            "Routes and transit are illustrative approved lanes. Inbound timing is assumed within the planning horizon.",
        ]
        closed_in_footprint = scenario_result.get("closed_stores_in_footprint") or []
        if closed_in_footprint:
            closed_ids = ", ".join(str(s.get("Store ID", "")) for s in closed_in_footprint)
            limitations.append(
                f"{len(closed_in_footprint)} Store(s) also fall within this alert's geography but are Closed "
                f"({closed_ids}); no operational action applies to them."
            )
        staffing_at_risk = staffing_df[staffing_df["Staffing Risk"].isin(["High", "Elevated"])] if not staffing_df.empty else pd.DataFrame()
        traffic_map = scenario_result.get("traffic_by_phase", {}) or {}
        impact_metrics = {
            "Affected stores": int(df["Store ID"].nunique()),
            "Products with inventory gap": int(gap_rows["UPC"].nunique()) if "UPC" in gap_rows.columns else int(len(gap_rows)),
            "Stores with inventory gap": int(gap_rows["Store ID"].nunique()),
            "Stores with route risk": int(df[df["Route Risk"] == "Yes"]["Store ID"].nunique()),
            "Stores with staffing risk": int(staffing_at_risk["Store ID"].nunique()) if not staffing_at_risk.empty else 0,
            "Scheduled staff": int(staffing_df["Scheduled Staff"].sum()) if not staffing_df.empty else 0,
            "Staff commute at risk": int(staffing_df["At-risk Commute Staff"].sum()) if not staffing_df.empty else 0,
            "Expected staff available": int(staffing_df["Expected Available Before Alert"].sum()) if not staffing_df.empty else 0,
            "Comparable scenario cases": int(scenario_result.get("comparable_events", 0) or 0),
            "Evidence ladder": match_type,
            "Event family": scenario_result.get("event_family", ""),
            "Hours until alert window": float(scenario_result.get("hours_until_event", 0) or 0),
            "Hours until alert ends": (
                float(scenario_result.get("hours_until_ends", 0) or 0)
                if scenario_result.get("hours_until_ends") is not None
                else None
            ),
            "Alert status": "Active now" if scenario_result.get("alert_is_active") else "Upcoming",
            "Alert duration hours": float(scenario_result.get("duration_hours", 0) or 0),
            "Pre-event customer store traffic vs normal": _traffic_pct_and_multiplier(traffic_map.get("Pre-Event", 0)),
            "During-event customer store traffic vs normal": _traffic_pct_and_multiplier(traffic_map.get("During-Event", 0)),
            "Post-event customer store traffic vs normal": _traffic_pct_and_multiplier(traffic_map.get("Post-Event", 0)),
        }
        affected_scope = weather_affected_scope(
            int(df["Store ID"].nunique()),
            scenario_result.get("state", ""),
            scenario_result.get("match_precision", ""),
        )
        actions = weather_recommended_actions(scenario_result)
        store_ids = sorted(df["Store ID"].astype(str).unique().tolist())

    return {
        "Incident ID": incident_id,
        "Type": "Weather",
        "Priority": priority,
        "Status": status,
        "Source": str(signal.get("source") or weather_result.get("source") or "NOAA Weather Alerts"),
        "Source Key": source_key,
        "Run ID": run_timestamp,
        "Event": scenario_result.get("event") or item.get("event", ""),
        "Match Type": match_type,
        "Match Reason": match_reason,
        "Store IDs": store_ids,
        "Evidence": {**signal, "alert": item},
        "Affected Scope": affected_scope,
        "Impact Metrics": impact_metrics,
        "Recommended Actions": actions,
        "Limitations": limitations,
        "Scenario": scenario_result,
        "Decision Status": "Proposed",
        "Provenance": DataProvenance(
            external_signal_mode="Scenario" if is_demo else "Live",
            internal_operations_mode="Synthetic",
            explanation_mode="Deterministic rules",
            as_of=run_timestamp or utc_now(),
            dataset_version="fixtures-v1",
        ).to_dict(),
    }


def build_weather_incidents(weather_result: Dict[str, Any], run_timestamp: str = "") -> List[Dict[str, Any]]:
    """Create one incident for each distinct NOAA Weather alert.

    Business rule:
      * one distinct NOAA alert = one Weather incident;
      * Store/product/DC/route/staffing findings remain details inside that incident;
      * no NOAA alert item = no Weather incident;
      * the same NOAA alert reuses the same stable Incident ID on refresh;
      * insufficient evidence or no internal match are assessment outcomes of the
        incident, not reasons to create extra incidents.
    """
    items = weather_result.get("items") or []
    rows = weather_result.get("rows") or []
    if not items:
        return []

    incidents: List[Dict[str, Any]] = []
    seen_incident_ids = set()
    # collect_weather_alerts_multi produces ONE row per queried state (region), each
    # carrying that state's own top_event/alert_count/severe_or_extreme_count/confidence/
    # recommended_action. Using rows[0] unconditionally as the base for every incident
    # meant a Georgia alert could display Texas's alert counts and top event -- whichever
    # state happened to be queried first. Look up each item's OWN state's row instead;
    # fall back to rows[0] only as a last resort when no per-state row can be matched
    # (e.g. a state whose collector call otherwise failed).
    aggregate_signal = rows[0] if rows else {}
    rows_by_region = {str(row.get("region", "")): row for row in rows if row.get("region")}
    ledger: Dict[tuple, float] = {}

    for item in sorted(items, key=lambda alert: str(alert.get("alert_id", ""))):
        item_states = item.get("source_query_states") or [aggregate_signal.get("region", "")]
        item_region = str(item_states[0] if item_states else aggregate_signal.get("region", ""))
        own_state_signal = rows_by_region.get(item_region, aggregate_signal)
        single_signal = dict(own_state_signal)
        single_signal.update(
            {
                "region": item_region,
                "source_query_states": item_states,
                "signal_value": 1,
                "risk_score": float(
                    item.get("risk_component", own_state_signal.get("risk_score", 0)) or 0
                ),
                "score_reason": (
                    f"{item.get('event', 'Weather alert')} for "
                    f"{item_region}; "
                    f"severity={item.get('severity', 'Unknown')}, "
                    f"urgency={item.get('urgency', 'Unknown')}, "
                    f"certainty={item.get('certainty', 'Unknown')}."
                ),
                "raw_reference": item.get("headline")
                or item.get("event")
                or "Weather alert",
            }
        )

        one_result = {
            **weather_result,
            "rows": [single_signal],
            "items": [item],
            "_atp_ledger": ledger,
            "assessment_time": run_timestamp or utc_now(),
        }
        # Deduplicate before reserving inventory.
        if any(str(existing.get("Source Key", "")) == str(item.get("alert_id", "")) for existing in incidents):
            continue
        incident = build_weather_incident(one_result, run_timestamp)

        # If the source payload contains the same NOAA alert more than once,
        # keep only one incident in the current run.
        if incident["Incident ID"] in seen_incident_ids:
            continue

        seen_incident_ids.add(incident["Incident ID"])
        incidents.append(incident)

    return incidents


def _footprint_metric(match_result: Dict[str, Any]) -> Any:
    """Stores inside the recall's distribution states -- candidates only, never "affected"."""
    if not match_result.get("distribution_known", False):
        return "Unknown distribution"
    return int(match_result.get("footprint_store_count", 0) or 0)


def build_recall_incidents(fda_result: Dict[str, Any], run_timestamp: str = "") -> List[Dict[str, Any]]:
    """Create one incident for each distinct openFDA recall case.

    Product/UPC/lot matching determines the assessment and impact detail, but a
    live recall remains one incident even when it has No Internal Match or only
    a Probable Match. Repeated source records in the same run are deduplicated.
    """
    items = fda_result.get("items") or []
    incidents = []
    seen_incident_ids = set()
    for idx, item in enumerate(items):
        match_result = recall_matching.match_recall_to_catalog(item)
        is_demo = bool(item.get("is_demo_scenario"))
        recall_source_key = str(item.get("recall_number") or item.get("event_id") or "").strip()
        if not recall_source_key:
            recall_source_key = "|".join([str(item.get("product", "")), str(item.get("upcs", "")), str(item.get("lots", "")), str(item.get("recall_date", ""))])
        id_material = f"demo|{recall_source_key}" if is_demo else f"live|{recall_source_key}"
        # 12 hex chars (48 bits), matching build_weather_incidents -- see its comment.
        incident_id = f"RC-{hashlib.md5(id_material.encode()).hexdigest()[:12].upper()}"
        status = match_result["match_status"]
        match_type = str(match_result.get("match_type") or status.replace("_", " ").title())
        match_reason = str(match_result.get("match_reason") or match_result.get("reason") or "")
        if status == "unmatched":
            # Consistency with Weather's no-Store-match policy (Principle: no-match is
            # not Low priority): "unmatched" means no internal catalog/UPC/lot evidence
            # was found at all, not that the recall was reviewed and judged low-severity.
            # A real "Low" would misrepresent an unassessed case as a graded decision.
            priority = ""
            impact_metrics = {
                "Evidence ladder": match_type,
                "Distribution-footprint stores (candidates)": _footprint_metric(match_result),
                "UPC/Lot-Confirmed Affected Stores": 0,
            }
            actions = [
                {
                    "Action": "No Product/Store action applies",
                    "Owner": "Compliance",
                    "Urgency": "None",
                    "Reason": match_reason or match_result.get("reason", "No internal catalog/UPC/lot match was found for this recall."),
                    "Evidence": "Recall matched against internal product catalog; no product/UPC/lot evidence found.",
                    "Decision Support": str(ladder_entry(status)["action"]),
                    "Status": "No action required",
                }
            ]
            limitations = [match_reason or match_result.get("reason", "")]
            affected_scope = "No internal product match"
        elif status in {"category_hazard_match", "distribution_match", "lot_review_required", "product_review_required", "distribution_review_required"}:
            priority = "High" if normalize_recall_classification(item.get("classification")) == "Class I" else "Medium"
            category = str(match_result.get("matched_category") or "")
            distribution_states = match_result.get("distribution_states") or []
            impact_metrics = {
                "Evidence ladder": match_type,
                "Confirmed UPC exposure": "No",
                "Distribution-footprint stores (candidates)": _footprint_metric(match_result),
                "UPC/Lot-Confirmed Affected Stores": "Pending lot confirmation" if status == "lot_review_required" else 0,
                "Category candidate": category or "Not identified",
                "Distribution state overlap": ", ".join(distribution_states) if distribution_states else "Not confirmed",
            }
            limitations = [
                match_reason or "Evidence is actionable for review, but Store/DC exposure is not quantified without a confirmed UPC/lot."
            ]
            if status == "product_review_required":
                impact_metrics["Candidate product"] = match_result.get("candidate_product") or "Not identified"
                impact_metrics["Internal Candidate UPC (Synthetic)"] = match_result.get("candidate_upc") or "Not identified"
                impact_metrics["Stores Carrying Possible Product Match"] = candidate_store_statement(match_result)
                impact_metrics["Brand/vendor check"] = "Found" if match_result.get("brand_matched") else "Not found"
                impact_metrics["Package size check"] = "Found" if match_result.get("size_matched") else "Not found"
                if match_result.get("candidate_estimate_available"):
                    impact_metrics["Estimated store on hand (planning only)"] = match_result["estimated_store_on_hand"]
                    impact_metrics["Estimated DC on hand (planning only)"] = match_result["estimated_dc_on_hand"]
                    impact_metrics["Estimated DC in transit (planning only)"] = match_result["estimated_dc_in_transit"]
                limitations.append("Text-only match: severity drives priority, not because the product match is confirmed. Estimated quantities are planning visibility only and are not confirmed exposure.")
            affected_scope = category or "Distribution overlap only"
            # Each status is a different open question -- a lot/UPC confirmation gap is not
            # a distribution-footprint gap, and naming the wrong one sends a buyer to check
            # the wrong thing while the Reason/Evidence text (built from the real status just
            # above) already says what actually needs confirming. This used to collapse to
            # "Distribution footprint review" for every status except category_hazard_match.
            action_name = {
                "category_hazard_match": "Category recall review",
                "distribution_match": "Distribution footprint review",
                "lot_review_required": "Lot confirmation review",
                "product_review_required": "Product/UPC confirmation review",
                "distribution_review_required": "Distribution footprint review",
            }[status]
            actions = [
                {
                    "Action": action_name,
                    "Owner": "Compliance + Category Buyer",
                    "Urgency": priority,
                    "Reason": match_reason,
                    "Evidence": f"Match type: {match_type}; category={category or 'not identified'}; states={', '.join(distribution_states) if distribution_states else 'not confirmed'}.",
                    "Decision Support": str(ladder_entry(status)["action"]),
                    "Rule": "Recall evidence ladder reached category/distribution match, but not exact UPC exposure.",
                    "Status": "Proposed",
                }
            ]
        else:
            priority = "High" if match_result["total_store_exposure"] > 0 or match_result["total_dc_in_transit"] > 0 else "Medium"
            limitations = ["Store, DC, sales, customer, substitute and supplier detail are synthetic. Lot inventory is a deterministic share of all known product lots, in whole estimated units that add back to the product-level totals, not observed lot-level stock."]
            impact_metrics = {
                "Evidence ladder": match_type,
                "Distribution-footprint stores (candidates)": _footprint_metric(match_result),
                "UPC/Lot-Confirmed Affected Stores": len({str(line.get("Store ID", "")) for line in match_result["store_lines"]}),
                "Store units on hand (est.)": match_result["total_store_exposure"],
                "DC units on hand (est.)": match_result["total_dc_on_hand"],
                "DC units in transit (est.)": match_result["total_dc_in_transit"],
                "Units already sold": match_result["units_sold"],
                "Loyalty-linked units sold": match_result["loyalty_units"],
                "Anonymous units sold": match_result["anonymous_units"],
                "Estimated financial exposure": match_result["financial_exposure"],
            }
            affected_scope = f"{match_result['matched_product']} ({len(match_result['store_lines'])} store-lot line(s))"
            actions = recall_recommended_actions(match_result)
        incident = {
            "Incident ID": incident_id,
            "Type": "Product Recall",
            "Priority": priority,
            "Status": status,
            "Source": "Demonstration scenario" if item.get("is_demo_scenario") else "openFDA",
            "Source Key": recall_source_key,
            "Run ID": run_timestamp,
            "Event": item.get("classification", "Recall"),
            "Store IDs": sorted(
                {
                    str(line.get("Store ID", ""))
                    for line in match_result.get("store_lines", [])
                    if str(line.get("Store ID", ""))
                }
            ),
            "Evidence": item,
            "Affected Scope": affected_scope,
            "Impact Metrics": impact_metrics,
            "Recommended Actions": actions,
            "Limitations": limitations,
            "Match": match_result,
            "Decision Status": "Proposed",
            "Provenance": DataProvenance(
                external_signal_mode="Scenario" if is_demo else "Live",
                internal_operations_mode="Synthetic",
                explanation_mode="Deterministic rules",
                as_of=run_timestamp or utc_now(),
                dataset_version="fixtures-v1",
            ).to_dict(),
        }

        if incident_id in seen_incident_ids:
            continue

        seen_incident_ids.add(incident_id)
        incidents.append(incident)

    return incidents
