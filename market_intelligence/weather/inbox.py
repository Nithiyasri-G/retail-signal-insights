from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any


@dataclass(frozen=True)
class WeatherInboxRow:
    incident_id: str = ""
    source_mode: str = ""
    event: str = ""
    match_type: str = ""
    match_reason: str = ""
    state: str = ""
    headline: str = ""
    alert_area: str = ""
    starts_at: str = ""
    ends_at: str = ""
    window: str = ""
    mapping_result: str = ""
    affected_stores: str = ""
    evidence_code: str = ""
    priority: str = ""
    decision_status: str = ""
    what_changed: str = ""
    geography: str = ""
    issuing_office: str = ""
    validity: str = ""
    noaa_profile: str = ""
    store_scope: str = ""
    store_scope_change: str = ""
    mapping_method: str = ""


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_display(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = _parse_datetime(text)
    if parsed is None:
        return text
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def _compact_utc(value: Any) -> str:
    """Compact UTC display used in the inbox to make same-type alerts scan quickly."""
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = _parse_datetime(text)
    if parsed is None:
        return text
    return parsed.strftime("%d %b %H:%M")


def shorten(value: str, limit: int = 72) -> str:
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _derive_evidence_code(incident: dict, scenario: dict) -> str:
    explicit = str(scenario.get("evidence_code") or "").strip()
    if explicit:
        return explicit
    status = str(incident.get("Status") or scenario.get("status") or "")
    reason = str(scenario.get("reason") or "").lower()
    precision = str(scenario.get("match_precision") or "").lower()
    if status == "ok" and "county" in precision:
        return "MATCH_COUNTY_NAME_FALLBACK"
    if "no demonstration store exists" in reason:
        return "STORE_COVERAGE_MISSING"
    if "area description" in reason or "county/polygon" in reason or "state only" in precision:
        return "NO_STORE_INTERSECTION"
    if "comparable" in reason:
        return "COMPARABLE_EVENTS_BELOW_THRESHOLD"
    return "EVIDENCE_UNCLASSIFIED"


def _identity_tokens(value: Any) -> list[str]:
    """Return equivalent NOAA alert identifiers for URL and URN forms."""
    text = str(value or "").strip()
    if not text:
        return []
    values = [text]
    marker = "/alerts/"
    if marker in text:
        suffix = text.split(marker, 1)[1].strip()
        if suffix:
            values.append(suffix)
    return list(dict.fromkeys(values))


def alert_identity_tokens(alert: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("alert_id", "id", "@id"):
        values.extend(_identity_tokens(alert.get(key)))
    return tuple(dict.fromkeys(values))


def referenced_alert_identity_tokens(alert: dict[str, Any]) -> tuple[str, ...]:
    """Return referenced NOAA alert IDs, newest reference first where sent time is available."""
    references = alert.get("references") or []
    if not isinstance(references, list):
        return ()

    def _sent_sort_key(ref: Any) -> str:
        if not isinstance(ref, dict):
            return ""
        return str(ref.get("sent") or "")

    ordered_refs = sorted(references, key=_sent_sort_key, reverse=True)
    values: list[str] = []
    for ref in ordered_refs:
        if isinstance(ref, dict):
            for key in ("@id", "identifier", "id"):
                values.extend(_identity_tokens(ref.get(key)))
        else:
            values.extend(_identity_tokens(ref))
    return tuple(dict.fromkeys(values))


def _alert_states(alert: dict[str, Any]) -> list[str]:
    """Prefer states encoded in NOAA UGC geography; fall back to query-state provenance."""
    states: list[str] = []
    geocode = alert.get("geocode") or {}
    ugc_values = geocode.get("UGC", []) if isinstance(geocode, dict) else []
    if isinstance(ugc_values, str):
        ugc_values = [ugc_values]
    for value in ugc_values or []:
        match = re.match(r"^([A-Z]{2})[CZ]\d{3}", str(value or "").upper())
        if match and match.group(1) not in states:
            states.append(match.group(1))

    if not states:
        query_states = alert.get("source_query_states") or []
        if isinstance(query_states, str):
            query_states = [query_states]
        for value in query_states:
            state = str(value or "").strip().upper()
            if re.fullmatch(r"[A-Z]{2}", state) and state not in states:
                states.append(state)
    return states


def _area_parts(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def summarize_alert_geography(alert: dict[str, Any], *, max_areas: int = 2) -> str:
    """Produce a compact geography label suitable for side-by-side same-event comparison."""
    states = _alert_states(alert)
    areas = _area_parts(alert.get("area_desc"))
    state_text = " + ".join(states)
    if not areas:
        return state_text or "Geography unavailable"

    shown = "; ".join(areas[:max_areas])
    remaining = max(0, len(areas) - max_areas)
    if remaining:
        suffix = "area" if remaining == 1 else "areas"
        shown = f"{shown} (+{remaining} {suffix})"
    return f"{state_text} · {shown}" if state_text else shown


def _issuing_office(alert: dict[str, Any]) -> str:
    sender_name = str(alert.get("sender_name") or alert.get("senderName") or "").strip()
    if sender_name:
        return sender_name
    headline = str(alert.get("headline") or "").strip()
    match = re.search(r"\bby\s+(NWS\s+.+)$", headline, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return str(alert.get("sender") or "").strip() or "Not supplied"


def _validity_label(alert: dict[str, Any]) -> str:
    sent = _compact_utc(alert.get("sent"))
    starts = _compact_utc(alert.get("onset") or alert.get("effective"))
    ends = _compact_utc(alert.get("ends") or alert.get("expires"))
    if sent and ends:
        return f"Issued {sent} · until {ends} UTC"
    if starts and ends:
        return f"{starts} → {ends} UTC"
    if starts:
        return f"Starts {starts} UTC"
    if ends:
        return f"Until {ends} UTC"
    return "Timing unavailable"


def _noaa_profile(alert: dict[str, Any]) -> str:
    """Severity · urgency · certainty, distinguishing "not classified" from "missing".

    "Unknown" is a value NOAA itself publishes, not an absent field, but printing
    "Unknown · Unknown · Unknown" reads like the app failed to fetch something.
    """
    values = [
        str(alert.get("severity") or "").strip(),
        str(alert.get("urgency") or "").strip(),
        str(alert.get("certainty") or "").strip(),
    ]
    values = [value for value in values if value]
    if not values:
        return "Not supplied"
    if all(value.lower() == "unknown" for value in values):
        return "Not classified by NOAA"
    return " · ".join(values)


def _mapping_label(scenario: dict[str, Any], evidence_code: str) -> str:
    precision = str(
        scenario.get("match_precision")
        or (scenario.get("geography_match") or {}).get("method")
        or ""
    ).strip()
    precision_lower = precision.lower()
    if "polygon" in precision_lower or evidence_code == "MATCH_ALERT_POLYGON":
        return "NOAA polygon"
    if "county" in precision_lower or evidence_code == "MATCH_COUNTY_NAME_FALLBACK":
        return "County fallback"
    if evidence_code == "NO_STORE_INTERSECTION":
        return "No Store intersection"
    if "state only" in precision_lower:
        return "State only"
    if evidence_code == "STORE_COVERAGE_MISSING":
        return "Store coverage missing"
    return precision or evidence_code.replace("_", " ").title()


def _store_ids(incident: dict[str, Any]) -> list[str]:
    return [str(value) for value in (incident.get("Store IDs") or []) if str(value)]


def _store_scope(incident: dict[str, Any], scenario: dict[str, Any]) -> str:
    stores = _store_ids(incident)
    if stores:
        noun = "Store" if len(stores) == 1 else "Stores"
        return f"{len(stores)} {noun} · {', '.join(stores)}"
    candidates = scenario.get("evaluated_stores") or scenario.get("candidate_stores") or []
    if candidates:
        return f"No Store match · {len(candidates)} evaluated"
    return "Not quantified"


def _store_scope_change(current: dict[str, Any], previous: dict[str, Any] | None, alert: dict[str, Any]) -> str:
    if previous is None:
        if str(alert.get("message_type") or "").lower() == "update" or alert.get("references"):
            return "Prior scope unavailable"
        return "New alert"

    current_ids = set(_store_ids(current))
    previous_ids = set(_store_ids(previous))
    added = sorted(current_ids - previous_ids)
    removed = sorted(previous_ids - current_ids)
    if not added and not removed:
        return "Unchanged"
    parts: list[str] = []
    if added:
        parts.append(f"+{len(added)} ({', '.join(added)})")
    if removed:
        parts.append(f"-{len(removed)} ({', '.join(removed)})")
    return " · ".join(parts)


def _format_end_change(current_end: Any, previous_end: Any) -> str:
    current_dt = _parse_datetime(current_end)
    previous_dt = _parse_datetime(previous_end)
    current_label = _compact_utc(current_end)
    if current_dt and previous_dt:
        if current_dt > previous_dt:
            return f"End extended to {current_label} UTC"
        if current_dt < previous_dt:
            return f"End shortened to {current_label} UTC"
    return "End time updated"


def _weather_change_summary(
    current_incident: dict[str, Any],
    previous_incident: dict[str, Any] | None,
    alert: dict[str, Any],
) -> str:
    if previous_incident is None:
        if str(alert.get("message_type") or "").lower() == "update" or alert.get("references"):
            return "NOAA update · prior content not in local history"
        return "New active alert"

    previous_alert = (previous_incident.get("Evidence") or {}).get("alert") or {}
    changes: list[str] = []

    current_areas = set(_area_parts(alert.get("area_desc")))
    previous_areas = set(_area_parts(previous_alert.get("area_desc")))
    added_areas = current_areas - previous_areas
    removed_areas = previous_areas - current_areas
    if added_areas or removed_areas:
        area_bits: list[str] = []
        if added_areas:
            area_bits.append(f"+{len(added_areas)} area{'s' if len(added_areas) != 1 else ''}")
        if removed_areas:
            area_bits.append(f"-{len(removed_areas)} area{'s' if len(removed_areas) != 1 else ''}")
        changes.append("Area " + "/".join(area_bits))

    current_end = alert.get("ends") or alert.get("expires")
    previous_end = previous_alert.get("ends") or previous_alert.get("expires")
    if str(current_end or "") != str(previous_end or "") and (current_end or previous_end):
        changes.append(_format_end_change(current_end, previous_end))

    for key, label in (("severity", "Severity"), ("urgency", "Urgency"), ("certainty", "Certainty")):
        current_value = str(alert.get(key) or "").strip()
        previous_value = str(previous_alert.get(key) or "").strip()
        if current_value and previous_value and current_value != previous_value:
            changes.append(f"{label} {previous_value}→{current_value}")

    if set(_store_ids(current_incident)) != set(_store_ids(previous_incident)) and not changes:
        changes.append("Store scope changed")

    return "; ".join(changes[:3]) if changes else "NOAA update · tracked scope unchanged"


def build_weather_inbox_row(
    incident: dict,
    previous_incident: dict | None = None,
) -> WeatherInboxRow:
    evidence = incident.get("Evidence") or {}
    alert = evidence.get("alert") or {}
    scenario = incident.get("Scenario") or {}
    stores = _store_ids(incident)
    candidates = scenario.get("candidate_stores") or []
    starts_at = _utc_display(alert.get("onset") or alert.get("effective"))
    ends_at = _utc_display(alert.get("ends") or alert.get("expires"))
    if starts_at and ends_at:
        window = f"{starts_at} → {ends_at}"
    else:
        window = starts_at or ends_at or "Timing unavailable"

    if stores:
        noun = "Store" if len(stores) == 1 else "Stores"
        mapping_result = f"{len(stores)} {noun} matched: {', '.join(stores)}"
        affected_stores = ", ".join(stores)
    elif candidates:
        mapping_result = f"No Store intersection; {len(candidates)} Stores evaluated"
        affected_stores = "None"
    else:
        mapping_result = "Store mapping unavailable"
        affected_stores = "None"

    evidence_code = _derive_evidence_code(incident, scenario)
    is_demo = bool(
        evidence.get("is_demo_scenario")
        or "demonstration" in str(incident.get("Source") or "").lower()
    )
    return WeatherInboxRow(
        incident_id=str(incident.get("Incident ID") or ""),
        source_mode="Controlled Demo" if is_demo else "Live NOAA",
        event=str(incident.get("Event") or alert.get("event") or ""),
        match_type=str(incident.get("Match Type") or scenario.get("match_type") or ""),
        # Not shortened: this value is carried straight into the CSV a reviewer opens
        # away from the app, and an ellipsis there is silent data loss. It is no longer
        # a table column, so it has no width to fit into.
        match_reason=str(incident.get("Match Reason") or scenario.get("match_reason") or scenario.get("reason") or ""),
        state=str(scenario.get("state") or evidence.get("region") or ""),
        headline=str(alert.get("headline") or ""),
        alert_area=str(alert.get("area_desc") or ""),
        starts_at=starts_at,
        ends_at=ends_at,
        window=window,
        mapping_result=mapping_result,
        affected_stores=affected_stores,
        evidence_code=evidence_code,
        priority=str(incident.get("Priority") or ""),
        decision_status=str(incident.get("Decision Status") or "Proposed"),
        what_changed=_weather_change_summary(incident, previous_incident, alert),
        geography=summarize_alert_geography(alert),
        issuing_office=_issuing_office(alert),
        validity=_validity_label(alert),
        noaa_profile=_noaa_profile(alert),
        store_scope=_store_scope(incident, scenario),
        store_scope_change=_store_scope_change(incident, previous_incident, alert),
        mapping_method=_mapping_label(scenario, evidence_code),
    )
