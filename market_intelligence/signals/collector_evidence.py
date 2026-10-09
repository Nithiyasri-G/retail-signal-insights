from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from market_intelligence.config.settings import APIFY_HARD_KEYWORD_LIMIT, APIFY_SAFE_TIME_RANGE, BLS_CPI_SERIES
from market_intelligence.config.sources import ANALYSIS_METHODS, SOURCE_ENDPOINTS, SOURCE_LABELS, SOURCE_ORDER


def count_raw_records(raw_payload: Any, items: Optional[List[Dict[str, Any]]] = None) -> int:
    """A04: `items` can already be a truncated/selected subset -- NOAA alerts after
    relevance-ranking and sample-limit truncation (W11), or Apify rows after region
    filtering -- so it is NOT the true "raw record count". Whenever raw_payload's shape
    is recognized, it is the genuine pre-filter count and must be preferred; `items` is
    only a fallback for payload shapes this function doesn't recognize at all.
    """
    if isinstance(raw_payload, list):
        return len(raw_payload)
    if isinstance(raw_payload, dict):
        if isinstance(raw_payload.get("features"), list):
            return len(raw_payload["features"])
        if isinstance(raw_payload.get("results"), list):
            return len(raw_payload["results"])
        series = raw_payload.get("Results", {}).get("series") if isinstance(raw_payload.get("Results"), dict) else None
        if isinstance(series, list):
            return sum(len(s.get("data", [])) for s in series if isinstance(s, dict))
    if items:
        return len(items)
    if raw_payload is None:
        return 0
    if isinstance(raw_payload, dict):
        nested_counts = [
            count_raw_records(value)
            for value in raw_payload.values()
            if isinstance(value, (dict, list))
        ]
        return sum(nested_counts) if nested_counts else 1
    return 1


def request_summary(source_key: str, run_config: Dict[str, Any]) -> str:
    if source_key == "gnews":
        keywords = run_config.get("news_keywords", [])
        return (
            f"{len(keywords)} keyword(s), country={run_config.get('country', '')}, "
            f"language={run_config.get('language', '')}, period={run_config.get('gnews_period', '')}, "
            f"max_results={run_config.get('max_news', '')}"
        )
    if source_key == "bls":
        return "series=" + ", ".join(BLS_CPI_SERIES.values())
    if source_key == "fda":
        return f"search={run_config.get('fda_query', '')}; limit={run_config.get('fda_limit', '')}"
    if source_key == "weather":
        return f"area={run_config.get('weather_area', '')}; limit={run_config.get('weather_limit', '')}; active NOAA alerts"
    if source_key == "apify":
        if not run_config.get("use_apify", False):
            return "Search-demand collector not enabled for this run"
        if not run_config.get("apify_token_present", False):
            return "Search-demand collector enabled, but no token was supplied"
        return (
            f"mode={run_config.get('apify_run_mode', 'Skip Apify')}; confirmed={'Yes' if run_config.get('apify_live_confirm', False) else 'No'}; "
            f"geo={run_config.get('apify_geo', '')}; time range={run_config.get('apify_time_range', APIFY_SAFE_TIME_RANGE)}; "
            f"max keywords={run_config.get('apify_max_keywords', APIFY_HARD_KEYWORD_LIMIT)}"
        )
    return ""


def readable_collector_note(value: Any) -> str:
    """Say what went wrong in a sentence instead of pasting the exception.

    A raw requests/urllib repr ("HTTPSConnectionPool(host='api.fda.gov', port=443):
    Max retries exceeded with url: ...") tells a client nothing and reads as a crash.
    The exact text stays in the run record; this is the on-screen version.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "The source did not respond in time. Re-run to retry."
    if "max retries" in lowered or "connection" in lowered or "nameresolution" in lowered or "resolve" in lowered:
        return "The source could not be reached from this network. Check connectivity and re-run."
    if "429" in text or "rate limit" in lowered:
        return "The source rate-limited this run. Wait and re-run."
    if "401" in text or "403" in text or "unauthorized" in lowered or "forbidden" in lowered:
        return "The source refused the request. Check the credentials for this source."
    if "404" in text:
        return "The source returned no matching records for this request."
    if any(code in text for code in ("500", "502", "503", "504")):
        return "The source reported a server-side error. Re-run to retry."
    if "json" in lowered and ("decode" in lowered or "expecting" in lowered):
        return "The source returned a response this app could not read."
    return text if " " in text and not text.endswith(")") else "The source returned an unexpected response."


def build_collector_evidence(results: Dict[str, Dict[str, Any]], run_config: Dict[str, Any], llm_audit: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    records = []
    llm_audit = llm_audit or {}
    accepted_signals = llm_audit.get("payload", {}).get("signals", []) if llm_audit.get("accepted_response") else []
    enabled_sources = run_config.get("enabled_sources", {})
    for source_key in SOURCE_ORDER:
        if source_key == "apify" and not enabled_sources.get("apify") and "apify" not in results:
            continue  # Signal Search is separate from the main assessment.
        result = results.get(source_key, {})
        status = result.get("status", "disabled" if not enabled_sources.get(source_key, False) else "not_run")
        normalized_rows = len(result.get("rows", []) or [])
        raw_records = count_raw_records(result.get("raw"), result.get("items"))
        live_request = (status in {"success", "failed", "empty"} or (source_key == "apify" and result.get("raw") is not None)) and not result.get("reused_saved_signal", False)
        mock_used = bool(result.get("mock_used", False) or result.get("meta", {}).get("mock_used", False))
        records.append(
            {
                "source": SOURCE_LABELS[source_key],
                "status": status,
                "endpoint_or_actor": SOURCE_ENDPOINTS[source_key],
                "request_scope": request_summary(source_key, run_config),
                "raw_records_pulled": raw_records,
                "normalized_feature_rows": normalized_rows,
                "live_request_made": "Yes" if live_request else "No",
                "source_represented_in_llm_payload": "Yes" if any(
                    str(row.get("source", "")) == str(sent.get("source", ""))
                    for row in result.get("rows", [])
                    for sent in accepted_signals
                ) else "No",
                "feature_rows_in_accepted_payload": sum(any(str(row.get("source", "")) == str(sent.get("source", "")) for row in result.get("rows", [])) for sent in accepted_signals),
                "article_rows_in_accepted_payload": len(llm_audit.get("payload", {}).get("supporting_news", [])) if source_key == "gnews" and llm_audit.get("accepted_response") else 0,
                "reused_saved_signal": "Yes" if result.get("reused_saved_signal") else "No",
                "mock_data_used": "Yes" if mock_used else "No",
                "analysis_method": ANALYSIS_METHODS[source_key],
                "error_or_note": readable_collector_note(result.get("error", "")),
            }
        )
    return records


def any_mock_used(results: Dict[str, Dict[str, Any]], llm_audit: Dict[str, Any]) -> bool:
    collector_mock = any(
        bool(result.get("mock_used", False) or result.get("meta", {}).get("mock_used", False))
        for result in results.values()
    )
    return collector_mock or bool(llm_audit.get("mock_data_used", False))
