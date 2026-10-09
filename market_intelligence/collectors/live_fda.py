from typing import Any
from typing import Dict

from market_intelligence.recalls.matching import recall_category_from_text
from market_intelligence.recalls.parsing import adjust_recall_score, classify_recall, extract_lots, extract_upcs, normalize_recall_classification
from market_intelligence.util.clock import utc_now

from market_intelligence.infra import http as http_client

def collect_fda_recalls(query: str, limit: int, retailer: str = "Retailer") -> Dict[str, Any]:
    params = {"search": f'({query}) AND status:"Ongoing"', "limit": limit, "sort": "recall_initiation_date:desc"}
    ok, data, msg = http_client.safe_request("https://api.fda.gov/food/enforcement.json", params=params, timeout=30)
    if not ok:
        if "404" in str(msg):
            return {"status": "empty", "source": "openFDA", "error": "No ongoing records matched this query.", "raw": data, "rows": [], "items": []}
        return {"status": "failed", "source": "openFDA", "error": msg, "raw": None, "rows": [], "items": []}
    results = [r for r in data.get("results", []) if str(r.get("status", "")).lower() == "ongoing"]
    rows = []
    items = []
    for item in results:
        risk_type, base_score = classify_recall(item.get("reason_for_recall", ""))
        product = item.get("product_description", "Unknown product")
        state = item.get("state", "US")
        classification = normalize_recall_classification(item.get("classification", ""))
        status = item.get("status", "")
        score = adjust_recall_score(base_score, classification, status)
        code_info = item.get("code_info", "")
        upcs = extract_upcs(f"{product} {code_info}")
        lots = extract_lots(code_info) or extract_lots(item.get("reason_for_recall", ""))
        items.append(
            {
                "product": product,
                "recall_number": item.get("recall_number", "") or item.get("event_id", ""),
                "event_id": item.get("event_id", ""),
                "reason": item.get("reason_for_recall", ""),
                "state": state,
                "classification": classification,
                "status": status,
                "recall_date": item.get("recall_initiation_date", ""),
                "distribution_pattern": item.get("distribution_pattern", ""),
                "recalling_firm": item.get("recalling_firm", ""),
                "upcs": ", ".join(upcs) if upcs else "",
                "lots": ", ".join(lots) if lots else "",
                "sku_match_status": "unknown",
                "risk_type": risk_type,
                "risk_score": score,
            }
        )
    if not items:
        return {"status": "empty", "source": "openFDA", "error": "No ongoing recall records in this response.", "raw": data, "rows": [], "items": []}
    aggregate_score = max([x["risk_score"] for x in items], default=1.0)
    ongoing_count = sum(1 for x in items if str(x.get("status", "")).lower() == "ongoing")
    class_i_count = sum(1 for x in items if x.get("classification") == "Class I")
    class_ii_count = sum(1 for x in items if x.get("classification") == "Class II")
    class_iii_count = sum(1 for x in items if x.get("classification") == "Class III")
    states = sorted({str(x.get("state", "")).strip() for x in items if str(x.get("state", "")).strip()})
    upc_count = sum(1 for x in items if x.get("upcs"))
    top_risk_type = max(items, key=lambda x: x["risk_score"]).get("risk_type", "none") if items else "none"
    signal = {
        "date": utc_now()[:10],
        "retailer": retailer,
        "region": "US",
        "region_scope": "national_with_state_records",
        "source": "openFDA",
        "signal_area": "Product Recalls",
        "signal_name": "recall_risk_score",
        "signal_value": len(items),
        "risk_score": round(aggregate_score, 2),
        "confidence": "High",
        "score_reason": f"Current scope: ongoing FDA recalls, newest initiation first (limited sample). Score uses highest adjusted recall severity. Inputs: {len(items)} records, {ongoing_count} ongoing, {class_i_count} Class I, {class_ii_count} Class II, {class_iii_count} Class III, {upc_count} records with UPCs, top risk type {str(top_risk_type).replace('_', ' ')}.",
        # openFDA's state is the recalling firm's location, not the distribution
        # footprint. Keep that distinction explicit so downstream geography matching
        # never presents firm state as affected-store coverage.
        "recalling_firm_states": ", ".join(states[:8]) if states else "Unknown",
        "distribution_coverage": "See FDA distribution pattern per recall record; not inferred from firm state.",
        "ongoing_count": ongoing_count,
        "class_i_count": class_i_count,
        "class_ii_count": class_ii_count,
        "class_iii_count": class_iii_count,
        "upc_record_count": upc_count,
        "sku_match_status": "unknown",
        "affected_category": "Food / snacks / candy / beverages",
        "catalog_categories": sorted(
            {
                category
                for item in items
                if (category := recall_category_from_text(item))
            }
        ),
        "business_impact": "Food and beverage recalls can trigger inventory withdrawal, substitution demand, and safety review.",
        "recommended_action": "Prioritize ongoing and Class I recalls, then match UPCs against internal inventory before store-level action.",
        "raw_reference": f"{len(items)} recall records; {ongoing_count} ongoing; {class_i_count} Class I; {class_ii_count} Class II",
    }
    rows.append(signal)
    return {"status": "success", "source": "openFDA", "error": "", "raw": data, "rows": rows, "items": items}
