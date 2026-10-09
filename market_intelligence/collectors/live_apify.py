from typing import Any
from typing import Dict
from typing import List

from market_intelligence.config.settings import APIFY_ALLOWED_TIME_RANGES, APIFY_HARD_KEYWORD_LIMIT, APIFY_SAFE_TIME_RANGE
from market_intelligence.infra.credentials import friendly_apify_failure
from market_intelligence.util.clock import utc_now


def get_apify_value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    if hasattr(obj, key):
        return getattr(obj, key)
    try:
        return obj[key]
    except (TypeError, KeyError, AttributeError):
        return None


def collect_apify_trends(
    token: str,
    keywords: List[str],
    geo: str,
    time_range: str,
    retailer: str = "Retailer",
    max_keywords: int = APIFY_HARD_KEYWORD_LIMIT,
    include_scored_signal: bool = True,
) -> Dict[str, Any]:
    if not token:
        return {"status": "skipped", "source": "Apify Trends", "error": "No Apify token provided.", "raw": None, "rows": [], "items": []}
    try:
        from apify_client import ApifyClient
    except ImportError:
        return {
            "status": "failed",
            "source": "Apify Trends",
            "error": "apify-client is not installed. Run: pip install apify-client (add it to requirements.txt so it's installed wherever this app is deployed).",
            "raw": None,
            "rows": [],
            "items": [],
        }
    try:
        client = ApifyClient(token)
        safe_max_keywords = min(max(1, int(max_keywords)), APIFY_HARD_KEYWORD_LIMIT)
        safe_time_range = time_range if time_range in APIFY_ALLOWED_TIME_RANGES else APIFY_SAFE_TIME_RANGE
        safe_time_range = safe_time_range or APIFY_SAFE_TIME_RANGE
        selected_keywords = [kw for kw in keywords if kw][:safe_max_keywords]
        if not selected_keywords:
            return {"status": "skipped", "source": "Apify Trends", "error": "No trend keywords provided.", "raw": None, "rows": [], "items": []}
        run_input = {"geo": geo, "searchTerms": selected_keywords, "timeRange": safe_time_range}
        run = client.actor("apify/google-trends-scraper").call(run_input=run_input)
        dataset_id = get_apify_value(run, "defaultDatasetId") or get_apify_value(run, "default_dataset_id")
        if not dataset_id:
            return {
                "status": "failed",
                "source": "Apify Trends",
                "error": "Apify run completed but no default dataset ID was found.",
                "raw": run_input,
                "rows": [],
                "items": [],
            }
        items = list(client.dataset(dataset_id).iterate_items())
    except Exception as exc:
        return {"status": "failed", "source": "Apify Trends", "error": friendly_apify_failure(str(exc)), "raw": None, "rows": [], "items": []}

    region_rows = []
    for item in items:
        keyword = item.get("searchTerm")
        for rank, region in enumerate(item.get("interestBySubregion", []) or [], start=1):
            values = region.get("value") or []
            if values:
                region_rows.append(
                    {
                        "keyword": keyword,
                        "region": region.get("geoName", ""),
                        "interest_score": values[0],
                        "rank": rank,
                    }
                )
    if not include_scored_signal:
        has_related = any(item.get("relatedQueries_top") or item.get("relatedQueries_rising") for item in items)
        if not region_rows and not has_related:
            return {"status": "empty", "source": "Apify Trends", "error": "No regional or related search findings returned for these terms and time window.", "raw": items, "rows": [], "items": []}
        return {"status": "success", "source": "Apify Trends", "error": "", "raw": items, "rows": [], "items": region_rows}
    if not region_rows:
        return {"status": "empty", "source": "Apify Trends", "error": "No regional search-interest findings returned for these terms and time window.", "raw": items, "rows": [], "items": []}
    top_score = max([float(x["interest_score"]) for x in region_rows], default=0.0)
    signal_score = round(min(10.0, max(1.0, top_score / 10.0)), 2) if top_score else 1.0
    signal = {
        "date": utc_now()[:10],
        "retailer": retailer,
        "region": geo,
        "region_scope": "trend_geo",
        "source": "Apify Google Trends",
        "signal_area": "Search Demand",
        "signal_name": "search_demand_score",
        "signal_value": top_score,
        "risk_score": signal_score,
        "confidence": "Medium",
        "score_reason": f"Score is top regional Google Trends interest divided by 10. Run hard-limited to {len(selected_keywords)} keyword(s) over {safe_time_range} to control Apify quota and memory.",
        "business_impact": "Search interest can reveal early demand shifts, promotional interest, and seasonal spikes.",
        "recommended_action": "Compare rising regions against sales, inventory, and competitor promotion calendars.",
        "raw_reference": f"{len(region_rows)} regional trend rows",
    }
    return {"status": "success", "source": "Apify Trends", "error": "", "raw": items, "rows": [signal], "items": region_rows}
