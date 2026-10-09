from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
import pandas as pd
import re

from market_intelligence.util.clock import utc_now

from market_intelligence.infra import http as http_client

def normalize_weather_area(area: str) -> str:
    candidate = re.sub(r"[^A-Za-z]", "", area or "").upper()
    if len(candidate) == 2:
        return candidate
    return "TX"


def normalize_weather_areas(area: str) -> List[str]:
    """Parse a comma/space-separated list of two-letter state codes.

    A single NOAA run only ever sees alerts for the state(s) actually queried, so
    querying one fixed state (the previous behavior, via normalize_weather_area) meant
    a live incident could only ever appear on a day that specific state had an active
    alert. This lets one run check several states -- e.g. every state with a demo
    store -- at once. Invalid/duplicate tokens are dropped; an empty result falls
    back to ["TX"] so existing single-state behavior/config is preserved.
    """
    tokens = re.split(r"[,\s]+", (area or "").strip())
    seen: List[str] = []
    for token in tokens:
        candidate = re.sub(r"[^A-Za-z]", "", token).upper()
        if len(candidate) == 2 and candidate not in seen:
            seen.append(candidate)
    return seen or ["TX"]


def weather_alert_weight(severity: str, urgency: str, certainty: str) -> float:
    severity_score = {
        "extreme": 5.0,
        "severe": 3.0,
        "moderate": 2.0,
        "minor": 1.0,
        "unknown": 1.0,
    }.get(str(severity or "").lower(), 1.0)
    urgency_bonus = {
        "immediate": 1.5,
        "expected": 0.75,
    }.get(str(urgency or "").lower(), 0.0)
    certainty_bonus = {
        "observed": 0.5,
        "likely": 0.5,
    }.get(str(certainty or "").lower(), 0.0)
    return severity_score + urgency_bonus + certainty_bonus


def _alert_feature_geocode_fips(props: Dict[str, Any]) -> set:
    """5-digit county FIPS codes carried by a raw NOAA feature's geocode block."""
    fips: set = set()
    geocode = props.get("geocode") or {}
    if not isinstance(geocode, dict):
        return fips
    for values in geocode.values():
        if not isinstance(values, (list, tuple)):
            continue
        for value in values:
            digits = "".join(ch for ch in str(value) if ch.isdigit())
            if len(digits) >= 5:
                fips.add(digits[-5:])
    return fips


def _alert_feature_is_footprint_relevant(props: Dict[str, Any], store_master: pd.DataFrame) -> bool:
    """Cheap relevance check for RANKING only (not the precise Store-match used later):
    does this raw NOAA feature's area text or geocode plausibly reach any Store's county?
    """
    area_desc = str(props.get("areaDesc", "") or "").lower()
    fips_values = _alert_feature_geocode_fips(props)
    for _, store in store_master.iterrows():
        county = str(store.get("County", "") or "").replace(" County", "").strip().lower()
        if county and re.search(rf"\b{re.escape(county)}\b", area_desc):
            return True
        if str(store.get("FIPS", "") or "") in fips_values:
            return True
    return False


def rank_alerts_by_operational_relevance(
    features: List[Dict[str, Any]],
    store_master: Optional[pd.DataFrame],
) -> Tuple[List[Dict[str, Any]], int]:
    """Order raw NOAA features so a Store-footprint-relevant alert is never displaced by
    an irrelevant one of higher severity (a plain severity sort could do exactly that).

    Geographic relevance to the Store footprint is checked FIRST; only within the same
    relevance tier does severity/urgency/certainty (via weather_alert_weight) decide
    order. When no store_master is supplied, relevance can't be assessed at all, so this
    degrades to severity-only ranking (still strictly better than raw API order, which
    carries no relevance signal whatsoever). Returns (ranked_features, relevant_count).
    """
    def _weight(feature: Dict[str, Any]) -> float:
        props = feature.get("properties", {}) if isinstance(feature, dict) else {}
        return weather_alert_weight(props.get("severity"), props.get("urgency"), props.get("certainty"))

    if store_master is None or store_master.empty:
        ranked = sorted(features, key=_weight, reverse=True)
        return ranked, 0

    relevance_flags = [
        _alert_feature_is_footprint_relevant(f.get("properties", {}) if isinstance(f, dict) else {}, store_master)
        for f in features
    ]
    relevant_count = sum(1 for flag in relevance_flags if flag)
    indexed = list(zip(features, relevance_flags))
    ranked = [
        f for f, _ in sorted(indexed, key=lambda pair: (0 if pair[1] else 1, -_weight(pair[0])))
    ]
    return ranked, relevant_count


def collect_weather_alerts(
    area: str,
    limit: int,
    retailer: str = "Retailer",
    store_master: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    state_area = normalize_weather_area(area)
    params = {"area": state_area}
    ok, data, msg = http_client.safe_request(
        "https://api.weather.gov/alerts/active",
        headers={"User-Agent": "MarketIntelligenceWorkbench/1.0", "Accept": "application/geo+json"},
        params=params,
        timeout=30,
    )
    if not ok:
        return {"status": "failed", "source": "NOAA Weather Alerts", "error": msg, "raw": None, "rows": [], "items": []}
    features = data.get("features", []) if isinstance(data, dict) else []
    # Rank BEFORE truncating -- the raw API response order is not a relevance ranking,
    # and taking "the first N" could silently drop a Store-footprint-relevant alert in
    # favor of an unrelated one that merely appeared earlier (or is more severe but
    # irrelevant) in NOAA's own ordering.
    ranked_features, relevant_in_full_set = rank_alerts_by_operational_relevance(features, store_master)
    selected_alerts = ranked_features[: max(1, int(limit))]
    items = []
    score_components = []
    severe_count = 0
    extreme_count = 0
    for alert in selected_alerts:
        props = alert.get("properties", {}) if isinstance(alert, dict) else {}
        severity = props.get("severity", "Unknown")
        urgency = props.get("urgency", "Unknown")
        certainty = props.get("certainty", "Unknown")
        component = weather_alert_weight(severity, urgency, certainty)
        score_components.append(component)
        severity_text = str(severity or "").lower()
        if severity_text == "extreme":
            extreme_count += 1
        if severity_text in {"severe", "extreme"}:
            severe_count += 1
        items.append(
            {
                "alert_id": alert.get("id", "") or props.get("id", "") or props.get("@id", ""),
                "event": props.get("event", ""),
                "severity": severity,
                "urgency": urgency,
                "certainty": certainty,
                "headline": props.get("headline", ""),
                "sender_name": props.get("senderName", ""),
                "sender": props.get("sender", ""),
                "area_desc": props.get("areaDesc", ""),
                "effective": props.get("effective", ""),
                "onset": props.get("onset", "") or props.get("effective", ""),
                "expires": props.get("expires", ""),
                "ends": props.get("ends", "") or props.get("expires", ""),
                "instruction": props.get("instruction", ""),
                "message_type": props.get("messageType", ""),
                "sent": props.get("sent", ""),
                "references": props.get("references", []),
                "affected_zones": props.get("affectedZones", []),
                "source_query_states": [state_area],
                "geometry": alert.get("geometry"),
                "geocode": props.get("geocode", {}),
                "risk_component": round(component, 2),
            }
        )
    risk_score = round(min(10.0, sum(score_components)), 2) if items else 0.0
    relevance_note = (
        f"; {relevant_in_full_set} of {len(features)} fetched alert(s) were Store-footprint-relevant"
        if store_master is not None
        else ""
    )
    if items:
        score_reason = (
            f"Score sums weighted active NOAA alerts for {state_area}, capped at 10. "
            f"Inputs: kept {len(items)} of {len(features)} fetched alert(s) (configured sample limit {limit}), "
            f"ranked by Store-footprint relevance then severity{relevance_note}, {severe_count} severe/extreme, {extreme_count} extreme; "
            f"severity/urgency/certainty components total {sum(score_components):.2f}."
        )
        raw_reference = f"{len(items)} active NOAA alert(s); top event: {items[0].get('event') or 'Unknown'}"
        recommended_action = "Check affected counties against store and DC routes; use alert severity as a short-horizon disruption and emergency-demand feature."
    else:
        score_reason = f"NOAA returned 0 active alerts for {state_area}. Score is 0 because no current weather disruption signal is present."
        raw_reference = "0 active NOAA alerts"
        recommended_action = "Keep weather feature at baseline for this state, then refresh before short-horizon replenishment decisions."
    signal = {
        "date": utc_now()[:10],
        "retailer": retailer,
        "region": state_area,
        "region_scope": "state_weather_alerts",
        "source": "NOAA Weather Alerts",
        "signal_area": "Weather Risk",
        "signal_name": "supply_chain_weather_risk_score",
        "signal_value": len(items),
        "risk_score": risk_score,
        "confidence": "High",
        "score_reason": score_reason,
        "alert_count": len(items),
        "total_returned_alert_count": len(features),
        "fetched_count": len(features),
        "retained_count": len(items),
        "operationally_relevant_fetched_count": relevant_in_full_set,
        "coverage": "Limited sample" if len(features) > len(items) else "All returned alerts",
        "top_event": str(items[0].get("event", "") or "") if items else "",
        "severe_or_extreme_count": severe_count,
        "extreme_count": extreme_count,
        "business_impact": "Active weather alerts can disrupt store traffic, DC-to-store routes, staffing, replenishment timing, and emergency-demand categories.",
        "recommended_action": recommended_action,
        "raw_reference": raw_reference,
    }
    return {"status": "success", "source": "NOAA Weather Alerts", "error": "", "raw": data, "rows": [signal], "items": items}


def collect_weather_alerts_multi(
    areas: List[str],
    limit: int,
    retailer: str = "Retailer",
    store_master: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """Query NOAA active alerts for each state in `areas` and merge into one result.

    A single collect_weather_alerts() call only ever sees alerts for one state, so a
    live incident could only appear on a day that one specific state had an active
    alert. Checking every state that has a demo store (or whatever the user configures)
    in one run makes the live path meaningfully more likely to find a match, without
    changing collect_weather_alerts itself or its single-area callers.
    """
    areas = areas or ["TX"]
    merged_items: List[Dict[str, Any]] = []
    merged_rows: List[Dict[str, Any]] = []
    raw_by_area: Dict[str, Any] = {}
    errors: List[str] = []
    any_success = False
    seen_alert_ids = set()

    for area in areas:
        result = collect_weather_alerts(area, limit, retailer, store_master=store_master)
        raw_by_area[area] = result.get("raw")
        if result.get("status") == "success":
            any_success = True
            for item in result.get("items", []):
                alert_id = str(item.get("alert_id") or "")
                if alert_id and alert_id in seen_alert_ids:
                    for existing in merged_items:
                        if str(existing.get("alert_id") or "") == alert_id:
                            existing["source_query_states"] = sorted(set(existing.get("source_query_states", [])) | set(item.get("source_query_states", [])))
                    continue
                if alert_id:
                    seen_alert_ids.add(alert_id)
                merged_items.append(item)
            merged_rows.extend(result.get("rows", []))
        else:
            errors.append(f"{area}: {result.get('error') or 'failed'}")

    if not any_success:
        return {
            "status": "failed",
            "source": "NOAA Weather Alerts",
            "error": "; ".join(errors) if errors else "All requested states failed.",
            "raw": raw_by_area,
            "rows": [],
            "items": [],
        }

    return {
        "status": "success",
        "source": f"NOAA Weather Alerts ({', '.join(areas)})",
        "error": "; ".join(errors),
        "raw": raw_by_area,
        "rows": merged_rows,
        "items": merged_items,
    }
