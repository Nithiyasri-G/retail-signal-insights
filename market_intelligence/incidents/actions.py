from typing import Any
from typing import Dict
from typing import List
import pandas as pd

from market_intelligence.weather.planning import traffic_phase_explanation


def weather_recommended_actions(scenario_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Create one staffing/labor decision per affected store, plus the traffic/labor timing
    plan below.

    Recommendations use already-calculated facts only; NVIDIA does not create them. The
    per-store supply-chain action that used to live here was removed: it restated the same
    Primary/Backup DC numbers as prose independently of the DC Replenishment Plan table, so
    the two could drift out of sync (see the Backup DC Status "Eligible"-vs-"Insufficient"
    fix). The DC Replenishment Plan table plus the AI-suggested plan below it
    (generate_dc_replenishment_recommendation) now cover that ground, grounded directly in
    the table's own columns.
    """
    if scenario_result.get("status") != "ok":
        return []
    df = scenario_result.get("scenario_df", pd.DataFrame())
    staffing_df = scenario_result.get("staffing_df", pd.DataFrame())
    traffic_df = scenario_result.get("traffic_df", pd.DataFrame())
    actions: List[Dict[str, Any]] = []

    for store_id, store_df in df.groupby("Store ID"):
        if not staffing_df.empty:
            srow = staffing_df[staffing_df["Store ID"] == store_id]
            if not srow.empty:
                srow = srow.iloc[0]
                staffing_risk = str(srow["Staffing Risk"])
                if staffing_risk in {"High", "Elevated"}:
                    actions.append(
                        {
                            "Action": "Staffing readiness / store-hours plan",
                            "Owner": "Store Operations",
                            "Urgency": "High" if staffing_risk == "High" else "Medium",
                            "Reason": (
                                f"{int(srow['At-risk Commute Staff'])} of {int(srow['Scheduled Staff'])} currently scheduled staff "
                                f"have commute exposure ({float(srow['Commute Risk %']) * 100:.0f}%). "
                                f"Expected available before the alert: {int(srow['Expected Available Before Alert'])}; "
                                f"on-call backup pool: {int(srow.get('On-call Backup Staff', 0))}."
                            ),
                            "Evidence": str(srow["Staffing Action"]),
                            "Decision Support": "Pre-arrange backup staff, bring lower-risk staff in before conditions deteriorate, and communicate adjusted store hours in advance if capacity is insufficient.",
                            "Status": "Proposed",
                        }
                    )

    if not traffic_df.empty:
        traffic_map = dict(zip(traffic_df["Phase"], traffic_df["Customer Store Traffic %"]))
        pre = float(traffic_map.get("Pre-Event", 0) or 0)
        during = float(traffic_map.get("During-Event", 0) or 0)
        post = float(traffic_map.get("Post-Event", 0) or 0)
        # W07: this sentence previously hardcoded "as customers stock up"/"as fewer
        # customers travel"/"as customers restock" regardless of that phase's actual
        # sign, and rounded the multiplier to 1 decimal while the traffic table above
        # rounds to 2 -- both now derive from the same sign-aware explanation and the
        # same .2f formatting as the table, so this sentence can never contradict it.
        def _traffic_clause(phase: str, value: float) -> str:
            explanation = traffic_phase_explanation(phase, value)
            return explanation.rstrip(".").lower() if explanation else "no material change"

        actions.append(
            {
                "Action": "Labor timing plan",
                "Owner": "Store Operations + Demand Planning",
                "Urgency": "Medium",
                "Reason": (
                    f"Customer store traffic for this event type is expected to run "
                    f"{pre:+.0f}% ({1 + pre / 100.0:.2f}x normal) pre-event ({_traffic_clause('Pre-Event', pre)}), "
                    f"{during:+.0f}% ({1 + during / 100.0:.2f}x normal) during-event ({_traffic_clause('During-Event', during)}), "
                    f"and {post:+.0f}% ({1 + post / 100.0:.2f}x normal) post-event ({_traffic_clause('Post-Event', post)})."
                ),
                "Evidence": "Versioned scenario assumption set.",
                "Decision Support": "Shift labor toward the pre-event surge, reduce exposure during the event where appropriate, and schedule rebound coverage after the event.",
                "Status": "Proposed",
            }
        )

    if not actions:
        actions.append(
            {
                "Action": "Monitor / post-event replenishment",
                "Owner": "Demand Planning",
                "Urgency": "Low",
                "Reason": "No inventory gap, route constraint, or staffing commute exposure requiring an immediate intervention was detected.",
                "Evidence": "Current scenario calculations.",
                "Decision Support": "Continue monitoring the alert window and post-event demand.",
                "Status": "Proposed",
            }
        )
    return actions


def recall_recommended_actions(match_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    if match_result.get("match_status") in (None, "unmatched"):
        return []
    actions: List[Dict[str, Any]] = []
    if match_result["match_status"] == "probable_text_match":
        actions.append(
            {
                "Action": "Mapping review required",
                "Owner": "Compliance + Category Buyer",
                "Urgency": "High",
                "Reason": f"Recall text probably matches {match_result['matched_product']} but no UPC/lot confirmed the match. Validate UPC, brand, package size and vendor before store action.",
                "Evidence": "Text-only match against the product catalog.",
                "Rule": "match_status == probable_text_match",
                "Status": "Proposed",
            }
        )
        return actions
    if match_result["total_store_exposure"] > 0:
        actions.append(
            {
                "Action": "POS block",
                "Owner": "Compliance",
                "Urgency": "High",
                "Reason": f"{match_result['total_store_exposure']:.0f} estimated units of {match_result['matched_product']} remain on shelf across affected stores.",
                "Evidence": "Store-level on-hand inventory for the matched UPC/lot.",
                "Rule": "confirmed match AND store on-hand > 0",
                "Status": "Proposed",
            }
        )
        actions.append(
            {
                "Action": "Store/DC withdrawal",
                "Owner": "Supply Chain + Compliance",
                "Urgency": "High",
                "Reason": "Isolate confirmed affected inventory and initiate withdrawal from stores and DC.",
                "Evidence": f"Store exposure {match_result['total_store_exposure']:.0f} estimated units; DC on-hand {match_result['total_dc_on_hand']:.0f} estimated units.",
                "Rule": "confirmed match AND (store or DC on-hand) > 0",
                "Status": "Proposed",
            }
        )
    if match_result["total_dc_in_transit"] > 0:
        actions.append(
            {
                "Action": "In-transit hold",
                "Owner": "Supply Chain",
                "Urgency": "High",
                "Reason": f"{match_result['total_dc_in_transit']:.0f} estimated in-transit units are affected.",
                "Evidence": "DC in-transit inventory for the matched UPC/lot.",
                "Rule": "confirmed match AND in-transit > 0",
                "Status": "Proposed",
            }
        )
    if match_result["loyalty_units"] > 0:
        actions.append(
            {
                "Action": "Customer notification",
                "Owner": "Compliance + Customer Care",
                "Urgency": "Medium",
                "Reason": f"{match_result['loyalty_customers']} tokenized loyalty customer(s) purchased {match_result['loyalty_units']} affected unit(s).",
                "Evidence": "Loyalty-linked purchase records for the matched lot.",
                "Rule": "confirmed match AND loyalty-linked units sold > 0",
                "Status": "Proposed",
            }
        )
    if match_result.get("substitute_product") and any(not s["Sufficient"] for s in match_result.get("substitute_readiness", [])):
        actions.append(
            {
                "Action": "Substitute replenishment",
                "Owner": "Category Buyer",
                "Urgency": "Medium",
                "Reason": f"Substitute {match_result['substitute_product']} is insufficiently stocked at one or more affected stores.",
                "Evidence": "Substitute-product inventory check across affected stores.",
                "Rule": "substitute mapped AND substitute on-hand <= 20 at >=1 store",
                "Status": "Proposed",
            }
        )
    if match_result.get("supplier_review_required"):
        actions.append(
            {
                "Action": "Supplier review",
                "Owner": "Compliance + Sourcing",
                "Urgency": "Medium",
                "Reason": f"{match_result['supplier_name']} has {match_result['supplier_prior_recalls']} prior recall(s).",
                "Evidence": "Supplier master prior-recall count.",
                "Rule": "supplier prior_recall_count >= 2",
                "Status": "Proposed",
            }
        )
    if not actions:
        actions.append(
            {
                "Action": "No action required",
                "Owner": "Compliance",
                "Urgency": "Low",
                "Reason": "Confirmed match but no remaining store/DC/in-transit exposure found.",
                "Evidence": "Zero on-hand, in-transit, and loyalty exposure for the matched UPC/lot.",
                "Rule": "confirmed match AND all exposure == 0",
                "Status": "Proposed",
            }
        )
    return actions
