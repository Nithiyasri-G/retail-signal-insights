from market_intelligence.ui.operational_impact import normalize_missing_evidence
from market_intelligence.weather.inbox import alert_identity_tokens
from market_intelligence.weather.inbox import build_weather_inbox_row
from market_intelligence.weather.inbox import referenced_alert_identity_tokens
from market_intelligence.weather.inbox import shorten
from market_intelligence.weather.wording import ROUTE_RISK_WARNING, route_risk_clause
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
import pandas as pd
import re
import sqlite3

from market_intelligence.data.demo import demo_store_master
from market_intelligence.persistence.operational_store import load_incident_payload, load_incidents
from market_intelligence.util.clock import utc_now
from market_intelligence.util.tables import format_recall_date, sum_numeric_column


def _weather_history_alert_index(limit: int = 500) -> Dict[str, Dict[str, Any]]:
    """Index persisted Weather incidents by NOAA alert identifier for update comparison.

    NOAA `references` links an Update product to earlier products.  We only compare
    against versions that this POC has actually persisted; we do not make extra NOAA
    calls while rendering the inbox.
    """
    index: Dict[str, Dict[str, Any]] = {}
    try:
        history = load_incidents(limit=limit)
    except (OSError, sqlite3.Error):
        return index
    if history is None or history.empty:
        return index

    weather_history = history[history["Type"] == "Weather"] if "Type" in history.columns else history
    for _, row in weather_history.iterrows():
        incident_id = str(row.get("Incident ID", "") or "")
        if not incident_id:
            continue
        payload = load_incident_payload(incident_id) or {}
        alert = (payload.get("Evidence") or {}).get("alert") or {}
        for token in alert_identity_tokens(alert):
            # load_incidents() is newest-first, so preserve the newest snapshot.
            index.setdefault(token, payload)
    return index


def _referenced_weather_incident(
    current_payload: Dict[str, Any],
    history_index: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    alert = (current_payload.get("Evidence") or {}).get("alert") or {}
    current_id = str(current_payload.get("Incident ID") or "")
    for token in referenced_alert_identity_tokens(alert):
        candidate = history_index.get(token)
        if candidate and str(candidate.get("Incident ID") or "") != current_id:
            return candidate
    return None


def enrich_weather_inbox(weather_df: pd.DataFrame) -> pd.DataFrame:
    """Add decision-useful Weather columns from each incident payload.

    The persisted inbox table stays lightweight; these fields are derived at render time so
    existing local SQLite databases do not need a destructive migration.
    """
    if weather_df is None or weather_df.empty:
        return weather_df
    rows = []
    history_alert_index = _weather_history_alert_index()
    for _, base in weather_df.iterrows():
        record = base.to_dict()
        incident_id = str(record.get("Incident ID", ""))
        payload = load_incident_payload(incident_id) or {}
        evidence = payload.get("Evidence", {}) or {}
        alert = evidence.get("alert", {}) or {}
        scenario = payload.get("Scenario", {}) or {}
        previous_payload = _referenced_weather_incident(payload, history_alert_index)
        inbox_row = build_weather_inbox_row(payload, previous_payload)
        scenario_df = scenario.get("scenario_df", pd.DataFrame())
        staffing_df = scenario.get("staffing_df", pd.DataFrame())

        if isinstance(scenario_df, list):
            scenario_df = pd.DataFrame(scenario_df)
        if isinstance(staffing_df, list):
            staffing_df = pd.DataFrame(staffing_df)

        record["Source Mode"] = "Controlled demo" if int(record.get("Demo", 0) or 0) == 1 else "Live NOAA"
        record["Headline"] = inbox_row.headline
        record["Alert Area"] = inbox_row.alert_area
        record["Window"] = inbox_row.window
        record["Store Mapping"] = inbox_row.mapping_result
        record["Evidence"] = inbox_row.evidence_code
        evidence_outcome = inbox_row.match_type or payload.get("Match Type", "")
        evidence_rationale = inbox_row.match_reason or payload.get("Match Reason", "")
        record["Match Type"] = evidence_outcome
        record["Match Reason"] = evidence_rationale
        record["What Changed"] = inbox_row.what_changed
        record["Geography"] = inbox_row.geography
        record["Issuing Office"] = inbox_row.issuing_office
        record["Validity"] = inbox_row.validity
        record["NOAA Profile"] = inbox_row.noaa_profile
        record["Store Scope"] = inbox_row.store_scope
        record["Store Scope Change"] = inbox_row.store_scope_change
        record["Mapping"] = inbox_row.mapping_method

        # Professional client-facing aliases used by the Weather Incident Inbox and CSV export.
        # The legacy keys above are retained internally so older persisted payloads and detail
        # screens continue to work without a database migration.
        record["Incident Reference"] = incident_id
        record["Signal Source"] = record.get("Source", "")
        record["Weather Event"] = record.get("Event", "")
        # Legacy aliases are retained for backward compatibility, but the visible
        # Weather Inbox now uses cleaner Store-match labels at the end of the table.
        record["Evidence Outcome"] = evidence_outcome
        record["Evidence Rationale"] = evidence_rationale
        record["Update Summary"] = inbox_row.what_changed
        record["Area of Impact"] = inbox_row.geography
        record["Issuing Authority"] = inbox_row.issuing_office
        # UTC timestamps alone do not tell a planner how much time is left. The relative
        # phrase is what drives urgency, and it is also the deadline the recommended
        # action is written against.
        timing_phrase, action_deadline = weather_alert_timing(alert)
        record["Alert Window"] = (
            f"{inbox_row.validity} · {timing_phrase}" if timing_phrase else inbox_row.validity
        )
        record["Action Deadline"] = action_deadline
        record["Alert Attributes"] = inbox_row.noaa_profile
        record["Store Exposure"] = weather_store_exposure_label(inbox_row.store_scope)
        record["Store Names"] = weather_store_name_list(record.get("Store IDs", ""))
        record["Exposure Change"] = inbox_row.store_scope_change
        # A blank priority read as missing data. "Monitor" says the alert was retained for
        # visibility and not operationally assessed, which is what no Store match means (it is
        # deliberately not "Low": nothing was assessed and found to be low risk).
        record["Operational Priority"] = str(record.get("Priority", "") or "").strip() or "Monitor"
        window_closed = timing_phrase == "window closed"
        decision_status = str(record.get("Decision Status", "") or "")
        record["Action Status"] = expired_action_status(decision_status, window_closed)
        record["Record Type"] = weather_record_type(
            demo=int(record.get("Demo", 0) or 0) == 1,
            window_closed=window_closed,
            store_count=len(_weather_store_ids_from_row(record)),
        )
        # One question per column. "Store Match" answers whether the alert reaches a Store
        # and how precisely; "Demand Basis" answers whether the uplift can be sized and from
        # what evidence. These used to share one "Store Match Type" column, so whichever
        # check failed first hid the other -- a county-only geography match could still be
        # reported under a Store heading as a "Direct Historical Match".
        record["Store Match"] = inbox_row.mapping_method
        record["Exposure Method"] = record["Store Match"]
        record["Demand Basis"] = evidence_outcome
        record["Store Match Type"] = record["Demand Basis"]
        record["Store Match Reason"] = evidence_rationale
        record["Alert Starts"] = inbox_row.starts_at
        record["Alert Ends"] = inbox_row.ends_at
        record["Severity"] = str(alert.get("severity", "") or "")
        record["Urgency"] = str(alert.get("urgency", "") or "")
        record["Certainty"] = str(alert.get("certainty", "") or "")
        record["Hours to Alert"] = scenario.get("hours_until_event", "") if scenario.get("status") == "ok" else ""

        if isinstance(scenario_df, pd.DataFrame) and not scenario_df.empty:
            record["Store Count"] = int(scenario_df["Store ID"].nunique())
            gap_df = scenario_df[scenario_df["Inventory Gap"] > 0] if "Inventory Gap" in scenario_df.columns else pd.DataFrame()
            record["Products at Risk"] = int(gap_df["UPC"].nunique()) if not gap_df.empty and "UPC" in gap_df.columns else 0
            record["Primary DC"] = ", ".join(sorted(set(scenario_df["Primary DC"].dropna().astype(str)))) if "Primary DC" in scenario_df.columns else ""
            # OIC-1: "Backup DC Status" (Eligible/Ineligible/No alternate) is the
            # explicit signal for whether a named Backup DC is actually supplying
            # units -- a DC named here while Ineligible/No alternate is shown for
            # transparency only, not as a real supplier, so naming it in the
            # recommended action would read as if a real backup DC were covering the
            # requirement.
            if "Backup DC" in scenario_df.columns:
                if "Backup DC Status" in scenario_df.columns:
                    eligible_backup_rows = scenario_df[scenario_df["Backup DC Status"] == "Eligible"]
                else:
                    eligible_backup_rows = scenario_df
                real_backups = {v for v in eligible_backup_rows["Backup DC"].dropna().astype(str) if v}
            else:
                real_backups = set()
            record["Backup DC"] = ", ".join(sorted(real_backups))
            record["Route Risk"] = "Yes" if "Route Risk" in scenario_df.columns and (scenario_df["Route Risk"] == "Yes").any() else "No"
            # The engine already computes an executable order quantity and the residual the
            # DC network cannot cover. Both belong in the recommended action, not only in
            # the Incident Detail tables. Order Quantity is the total requirement -- it is
            # NOT all sourced from the Primary DC, so Primary/Backup Planned Qty are carried
            # separately: the recommended action must name the DC that actually supplies each
            # portion, the same split the DC Replenishment Plan shows, rather than crediting
            # the full quantity to the Primary DC regardless of what it can actually supply.
            record["Units To Move"] = sum_numeric_column(scenario_df, "Order Quantity")
            record["Residual Units"] = sum_numeric_column(scenario_df, "Residual Gap")
            record["Primary Planned Units"] = sum_numeric_column(scenario_df, "Primary Planned Qty")
            record["Backup Planned Units"] = sum_numeric_column(scenario_df, "Backup Planned Qty")
        else:
            store_ids = [x.strip() for x in str(record.get("Store IDs", "")).split(",") if x.strip()]
            record["Store Count"] = len(store_ids)
            record["Products at Risk"] = ""
            record["Primary DC"] = ""
            record["Backup DC"] = ""
            record["Route Risk"] = "Not assessed"
            record["Units To Move"] = 0.0
            record["Residual Units"] = 0.0
            record["Primary Planned Units"] = 0.0
            record["Backup Planned Units"] = 0.0

        if isinstance(staffing_df, pd.DataFrame) and not staffing_df.empty:
            risks = staffing_df["Staffing Risk"].astype(str).tolist() if "Staffing Risk" in staffing_df.columns else []
            record["Staffing Risk"] = "High" if "High" in risks else "Elevated" if "Elevated" in risks else "Low"
            record["Staff at Risk"] = int(sum_numeric_column(staffing_df, "At-risk Commute Staff"))
            record["Staff Available"] = int(sum_numeric_column(staffing_df, "Expected Available Before Alert"))
        else:
            record["Staffing Risk"] = "Not assessed"
            record["Staff at Risk"] = ""
            record["Staff Available"] = ""
        # Planner-facing evidence status for the inbox.
        # The live alert is still retained as an incident even when the current
        # internal demonstration data cannot yet support a quantified impact.
        payload_status = str(payload.get("Status") or record.get("Status") or "")
        scenario_reason = str(scenario.get("reason") or "").strip()
        comparable_events = int(scenario.get("comparable_events", 0) or 0)
        match_precision = str(scenario.get("match_precision", "") or "")

        if payload_status == "ok":
            record["Assessment"] = record.get("Match Type") or "Matched / Quantified"
            record["Missing Evidence"] = "None"
        elif payload_status == "no_match":
            record["Assessment"] = "No Internal Match"
            if "No demonstration store exists" in scenario_reason:
                record["Missing Evidence"] = "Store coverage for NOAA alert geography"
            else:
                record["Missing Evidence"] = "Store / geography match"
        elif payload_status == "insufficient_evidence":
            record["Assessment"] = record.get("Match Type") or "Evidence Limited"
            reason_lower = scenario_reason.lower()
            if (
                "state only" in match_precision.lower()
                or "county/polygon" in reason_lower
                or "area description" in reason_lower
            ):
                record["Missing Evidence"] = normalize_missing_evidence(
                    "Precise NOAA geography → Store match"
                )
            elif comparable_events < 3:
                record["Missing Evidence"] = (
                    f"Comparable history: {comparable_events} found; 3 required"
                )
            else:
                record["Missing Evidence"] = scenario_reason or "Additional internal evidence"
        else:
            record["Assessment"] = payload_status.replace("_", " ").title() or "Not Assessed"
            record["Missing Evidence"] = scenario_reason or ""

        record["Incident Summary"] = weather_incident_summary(record)
        record["Actionability"] = weather_actionability(record)
        record["Recommended Action"] = weather_recommended_action(record)
        record["Evidence Strength"] = weather_evidence_strength(record)

        rows.append(record)
    return pd.DataFrame(rows)


_WEATHER_STORE_LOCATIONS: Dict[str, str] = {}


def _weather_store_locations() -> Dict[str, str]:
    """Store ID -> human location, so the inbox names Stores instead of bare fixture IDs.

    A planner knows a Store by its town, not by "108". The Store master already carries
    a name of the form "Store 108 - Midland, TX"; only the location half is needed here.
    """
    if not _WEATHER_STORE_LOCATIONS:
        try:
            master = demo_store_master()
        except Exception:
            return _WEATHER_STORE_LOCATIONS
        for _, store in master.iterrows():
            name = str(store.get("Store Name", "") or "").strip()
            location = name.split(" - ", 1)[1].strip() if " - " in name else name
            store_id = str(store.get("Store ID", "") or "").strip()
            if store_id:
                _WEATHER_STORE_LOCATIONS[store_id] = location
    return _WEATHER_STORE_LOCATIONS


def weather_store_name_list(store_ids: Any) -> str:
    lookup = _weather_store_locations()
    ids = [x.strip() for x in str(store_ids or "").split(",") if x.strip()]
    return "; ".join(f"{sid} ({lookup[sid]})" if lookup.get(sid) else sid for sid in ids)


def weather_store_exposure_label(store_scope: str) -> str:
    """Re-render "2 Stores · 101, 104" as "2 Stores · 101 (Dallas, TX), 104 (Houston, TX)"."""
    text = str(store_scope or "")
    head, separator, tail = text.partition(" · ")
    if not separator:
        return text
    ids = [x.strip() for x in tail.split(",")]
    if not ids or not all(re.fullmatch(r"\d{2,6}", value or "") for value in ids):
        return text
    lookup = _weather_store_locations()
    named = [f"{sid} ({lookup[sid]})" if lookup.get(sid) else sid for sid in ids]
    return f"{head} · {', '.join(named)}"


def _weather_duration(hours: float) -> str:
    if hours < 1:
        return f"{max(1, int(round(hours * 60)))} min"
    if hours < 48:
        return f"{hours:.0f}h"
    return f"{hours / 24:.0f} days"


def weather_alert_timing(alert: Dict[str, Any]) -> Tuple[str, str]:
    """Return (relative phrase for the alert window, deadline phrase for the action).

    NOAA timestamps are UTC. "until 30 Sep 12:00 UTC" does not tell a US planner how
    much time is left, and time left is what decides whether a replenishment can still
    land. An alert that is already in effect is measured against its end, not its start.
    """
    now = pd.Timestamp.now(tz="UTC")
    onset = pd.to_datetime(alert.get("onset") or alert.get("effective"), utc=True, errors="coerce")
    ends = pd.to_datetime(alert.get("ends") or alert.get("expires"), utc=True, errors="coerce")

    to_onset = None if pd.isna(onset) else (onset - now).total_seconds() / 3600.0
    to_end = None if pd.isna(ends) else (ends - now).total_seconds() / 3600.0

    if to_onset is not None and to_onset > 0:
        window = _weather_duration(to_onset)
        return f"starts in {window}", f"within {window}, before the alert begins"
    if to_end is not None and to_end > 0:
        window = _weather_duration(to_end)
        return f"active now, ends in {window}", f"within {window}, before the alert ends"
    if to_end is not None:
        return "window closed", "immediately -- the alert window has already closed"
    return "", "before the alert window closes"


def expired_action_status(decision_status: str, window_closed: bool) -> str:
    """A closed alert with no recorded decision is not still "Proposed".

    A recorded decision (Approved, Modified, Rejected) is kept as it is; only the open
    "Proposed" state is replaced, because a decision can no longer be taken on an alert that is over.
    The original priority stays on the row for historical reference.
    """
    if window_closed and decision_status.strip().lower() in {"", "proposed"}:
        return "Expired - closed without action"
    return decision_status


def weather_record_type(demo: bool, window_closed: bool, store_count: int) -> str:
    """One word-level classification so live, expired, no-match and demonstration rows are told apart."""
    if demo:
        return "Controlled demonstration"
    if window_closed:
        return "Expired incident"
    if store_count == 0:
        return "Live - no Store intersection"
    return "Live external incident"


def _weather_text(value: Any, default: str = "Not Available") -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "null", "not quantified"}:
        return default
    return text


def _weather_store_ids_from_row(row: Dict[str, Any]) -> List[str]:
    raw = str(row.get("Store IDs", "") or "")
    store_ids = [x.strip() for x in raw.split(",") if x.strip()]
    if store_ids:
        return store_ids
    exposure = str(row.get("Store Exposure") or row.get("Store Scope") or "")
    return re.findall(r"\b\d{3,6}\b", exposure)


def _weather_store_count(row: Dict[str, Any]) -> int:
    try:
        value = row.get("Store Count")
        if value not in (None, "") and not pd.isna(value):
            return int(float(value))
    except Exception:
        pass
    return len(_weather_store_ids_from_row(row))


def _weather_store_match(row: Dict[str, Any]) -> str:
    """The geography answer only: did a Store fall inside this alert, and how precisely."""
    return str(
        row.get("Store Match")
        or row.get("Exposure Method")
        or row.get("Mapping")
        or row.get("Match Precision")
        or ""
    )


def _weather_demand_basis(row: Dict[str, Any]) -> str:
    """The demand-evidence answer only: can the uplift be sized, and from what evidence."""
    return str(
        row.get("Demand Basis")
        or row.get("Store Match Type")
        or row.get("Match Type")
        or row.get("Assessment")
        or ""
    )


def _weather_no_demand_basis(row: Dict[str, Any]) -> bool:
    basis = _weather_demand_basis(row).lower()
    return any(token in basis for token in ("not assessed", "no internal", "no store"))


def weather_evidence_strength(row: Dict[str, Any]) -> str:
    method = _weather_store_match(row).lower()
    basis = _weather_demand_basis(row).lower()
    if _weather_no_demand_basis(row) or "state only" in method or "no store" in method:
        return "Limited"
    if "polygon" in method and any(token in basis for token in ["exact", "direct", "matched"]):
        return "Strong"
    if any(token in method for token in ["polygon", "county", "zone"]) or any(
        token in basis for token in ["analog", "exposure", "evidence", "scenario"]
    ):
        return "Moderate"
    return "Limited"


def weather_actionability(row: Dict[str, Any]) -> str:
    priority = str(row.get("Operational Priority") or row.get("Priority") or "").lower()
    store_count = _weather_store_count(row)
    evidence = weather_evidence_strength(row).lower()
    if priority.startswith("high") and store_count > 0 and not _weather_no_demand_basis(row):
        return "Act"
    if store_count > 0 or priority.startswith("high") or evidence == "moderate":
        return "Review"
    return "Monitor"


def _weather_store_phrase(row: Dict[str, Any], limit: int = 3) -> str:
    """Name the stores rather than just counting them -- a planner acts on stores, not totals."""
    names = [n for n in str(row.get("Store Names", "") or "").split("; ") if n.strip()]
    if not names:
        names = _weather_store_ids_from_row(row)
    if not names:
        return "the exposed Stores"
    shown = names[:limit]
    remainder = len(names) - len(shown)
    text = ", ".join(shown)
    if remainder > 0:
        text += f" and {remainder} more"
    noun = "Store" if len(names) == 1 else "Stores"
    return f"{noun} {text}"


def _weather_deadline_phrase(row: Dict[str, Any]) -> str:
    """Give the action a clock. The alert's own timing is the only deadline that matters."""
    deadline = str(row.get("Action Deadline", "") or "").strip()
    return deadline or "before the alert window closes"


def _weather_float(row: Dict[str, Any], key: str) -> float:
    try:
        value = row.get(key)
        if value in (None, "") or pd.isna(value):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def replenishment_serving_dc(row: Dict[str, Any]) -> str:
    """Name only the DC(s) that actually plan to ship for this Store/Product row.

    Ajith's product-level requirement was to show the actual serving/selected DC,
    available quantity and planned quantity per Store/Product -- not every DC that was
    merely considered. A DC that ships zero units is never named here, so the table
    can't be misread as "one DC for everything" when a different DC, or none at all, is
    really covering the requirement.
    """
    primary_qty = _weather_float(row, "Primary Planned Qty")
    backup_qty = _weather_float(row, "Backup Planned Qty")
    primary_dc = str(row.get("Primary DC", "") or "").strip()
    backup_dc = str(row.get("Backup DC", "") or "").strip()
    names: List[str] = []
    if primary_qty > 0 and primary_dc:
        names.append(primary_dc)
    if backup_qty > 0 and backup_dc:
        names.append(backup_dc)
    if names:
        return " + ".join(names)
    return "None available" if _weather_float(row, "Order Quantity") > 0 else "Not needed"


def replenishment_available_qty(row: Dict[str, Any]) -> float:
    """ATP of whichever DC(s) are named in Serving DC.

    Falls back to the normally-assigned Primary DC's ATP when nothing could ship, so the
    number always answers "how much stock did the network have to work with", not just
    "how much of it got used".
    """
    primary_qty = _weather_float(row, "Primary Planned Qty")
    backup_qty = _weather_float(row, "Backup Planned Qty")
    primary_atp = _weather_float(row, "Primary DC Available ATP")
    backup_atp = _weather_float(row, "Backup DC Available ATP")
    if primary_qty > 0 and backup_qty > 0:
        return primary_atp + backup_atp
    if backup_qty > 0:
        return backup_atp
    return primary_atp


def replenishment_planned_qty(row: Dict[str, Any]) -> float:
    return _weather_float(row, "Primary Planned Qty") + _weather_float(row, "Backup Planned Qty")


def replenishment_action_text(row: Dict[str, Any]) -> str:
    """Fold Route Risk's Yes/No into the Replenishment Plan sentence.

    Replenishment Plan already names the serving DC(s), the units and the allocation
    rule (including the single-DC-over-split-shipment preference, e.g. "Nearest eligible
    alternate supplies the complete requirement to avoid a split shipment."); this appends
    the route-risk warning to the same sentence instead of a separate column, so the whole
    story -- what ships, from where, and what to watch -- reads in one place.
    """
    text = str(row.get("Replenishment Plan", "") or "").strip()
    if str(row.get("Route Risk", "")).strip().lower() == "yes":
        risk_note = f"The primary delivery route is weather-exposed. {ROUTE_RISK_WARNING}."
        text = f"{text} {risk_note}" if text else risk_note
    return text


def weather_recommended_action(row: Dict[str, Any]) -> str:
    """Name the stores, the quantity, the DC and the deadline.

    A row that says only "review inventory" is indistinguishable from every other row
    and gives a planner nothing to execute, so the specific facts the engine already
    computed (order quantity, primary DC, residual gap, alert timing) are stated here.

    Order Quantity is the total requirement, not what any one DC actually supplies --
    the DC Replenishment Plan splits it into Primary Planned Qty, Backup Planned Qty and
    a Residual Gap, and can plan 0 units from the Primary DC. Crediting the Primary DC
    with the full Order Quantity regardless of that split is exactly the inconsistency
    Ajith would catch: the inbox naming a release from a DC the replenishment table shows
    supplying nothing. So the recommended action names each DC only for the units the
    plan actually assigns to it, in the same Primary-then-Backup-then-escalate order the
    DC Replenishment Plan itself uses.
    """
    action = weather_actionability(row)
    basis = _weather_demand_basis(row)
    stores = _weather_store_phrase(row)
    deadline = _weather_deadline_phrase(row)

    if action == "Act":
        units = _weather_float(row, "Units To Move")
        residual = _weather_float(row, "Residual Units")
        primary_planned = _weather_float(row, "Primary Planned Units")
        backup_planned = _weather_float(row, "Backup Planned Units")
        primary_dc = str(row.get("Primary DC", "") or "").strip()
        backup_dc = str(row.get("Backup DC", "") or "").strip()
        parts: List[str] = []
        if units > 0:
            releases: List[str] = []
            if primary_planned > 0:
                from_dc = f" from {primary_dc}" if primary_dc else ""
                releases.append(f"{primary_planned:,.0f} units{from_dc}")
            if backup_planned > 0:
                from_dc = f" from {backup_dc}" if backup_dc else ""
                releases.append(f"{backup_planned:,.0f} units{from_dc}")
            if releases:
                parts.append(f"Release {' and '.join(releases)} to {stores} {deadline}")
                if residual > 0:
                    parts.append(f"escalate the remaining {residual:,.0f} units for additional DC or supplier cover")
            else:
                # Neither DC can supply anything -- the full requirement is unmet, so there
                # is nothing to "release"; say so plainly instead of naming a DC that ships 0.
                parts.append(f"No DC can supply {stores} within the window; escalate all {units:,.0f} units for additional DC or supplier cover")
        else:
            parts.append(f"Confirm inventory cover for {stores} {deadline}")
        if str(row.get("Route Risk", "")).strip().lower() == "yes":
            parts.append(route_risk_clause())
        staffing_risk = str(row.get("Staffing Risk", "") or "").strip().lower()
        if staffing_risk in {"high", "elevated"}:
            staff_at_risk = _weather_float(row, "Staff at Risk")
            staff_text = f"{staff_at_risk:,.0f} scheduled staff have commute exposure" if staff_at_risk else "staffing is exposed"
            parts.append(f"{staff_text}, so confirm backup cover")
        return "; ".join(parts) + "."

    if action == "Review":
        if "Analog" in basis:
            return (
                f"Validate the analog scenario evidence for {stores} before applying any demand uplift, {deadline}."
            )
        if "Exposure" in basis:
            return (
                f"{stores} are exposed but the uplift cannot be sized; confirm cover from recent sell-through {deadline}."
            )
        if _weather_no_demand_basis(row):
            return "Confirm whether any Store falls inside this alert before planning a response."
        return f"Confirm Store exposure and internal evidence for {stores} {deadline}."

    return "Monitor only; no Store matched this alert geography, so no Store action applies."


def weather_incident_summary(row: Dict[str, Any]) -> str:
    event = _weather_text(row.get("Weather Event") or row.get("Event"), "Weather alert")
    area = _weather_text(row.get("Area of Impact") or row.get("Geography") or row.get("Affected Scope"), "selected area")
    method = _weather_text(_weather_store_match(row), "Store match pending")
    store_count = _weather_store_count(row)
    if store_count > 0:
        store_text = f"{store_count} Store{'s' if store_count != 1 else ''} exposed"
    else:
        store_text = "Store exposure not confirmed"
    return shorten(f"{event} in {area}; {store_text} via {method}.", 150)


def weather_inbox_export_csv(inbox_df: pd.DataFrame, visible_cols: List[str]) -> bytes:
    """Export the inbox with the run context attached to every row.

    A CSV that leaves this screen is read by someone who never saw the screen. Without
    the run time and the provenance of each layer, a reader cannot tell live NOAA data
    from synthetic Store fixtures, and the figures look more settled than they are.
    """
    export = inbox_df[[c for c in visible_cols if c in inbox_df.columns]].copy()
    if "Incident Summary" in inbox_df.columns:
        export["Incident Summary"] = inbox_df["Incident Summary"]
    if "Store Match Reason" in inbox_df.columns:
        export["Demand Basis Reason"] = inbox_df["Store Match Reason"]
    export["Run Time (UTC)"] = utc_now()
    export["External Signal"] = "Live NOAA alerts"
    export["Internal Operations Data"] = "Synthetic internal dataset"
    export["Dataset Version"] = "fixtures-v1"
    return export.to_csv(index=False).encode("utf-8")


def enrich_recall_history(recall_df: pd.DataFrame) -> pd.DataFrame:
    """Add the openFDA fields that tell two recalls of one product apart.

    The persisted history row carries only the product name, so a run with five Peanut
    Butter recalls produced five identical lines. Recall number, issuing firm, reason and
    date all come back from the stored payload; nothing new has to be collected.
    """
    if recall_df is None or recall_df.empty:
        return recall_df
    rows = []
    for _, base in recall_df.iterrows():
        record = base.to_dict()
        payload = load_incident_payload(str(record.get("Incident ID", ""))) or {}
        evidence = payload.get("Evidence", {}) or {}
        record["Recall Number"] = str(evidence.get("recall_number") or evidence.get("event_id") or "")
        record["Recalling Firm"] = str(evidence.get("recalling_firm") or "")
        record["Recall Reason"] = shorten(str(evidence.get("reason") or ""), 90)
        record["Recall Date"] = format_recall_date(evidence.get("recall_date"))
        record["Classification"] = str(evidence.get("classification") or payload.get("Event") or "")
        rows.append(record)
    return pd.DataFrame(rows)
