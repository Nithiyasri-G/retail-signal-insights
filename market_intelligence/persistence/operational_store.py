from typing import Any
from typing import Dict
from typing import Optional
import json
import pandas as pd
import sqlite3

from market_intelligence.config.logging_setup import log_operational_event
from market_intelligence.util.clock import utc_now
from market_intelligence.util.hashing import payload_hash

from market_intelligence.config import settings

def init_operational_impact_db() -> None:
    with sqlite3.connect(settings.RUN_HISTORY_DB, timeout=10) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS operational_incidents (
                incident_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                incident_type TEXT NOT NULL,
                priority TEXT,
                status TEXT,
                affected_scope TEXT,
                payload_json TEXT,
                decision_status TEXT DEFAULT 'Proposed',
                state TEXT,
                product TEXT,
                event TEXT,
                store_ids TEXT,
                run_id TEXT,
                source_key TEXT,
                is_demo INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS operational_decisions (
                decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                actor TEXT,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                modified_action TEXT
            )
            """
        )
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(operational_incidents)").fetchall()}
        required_cols = {
            "state": "TEXT",
            "product": "TEXT",
            "event": "TEXT",
            "store_ids": "TEXT",
            "run_id": "TEXT",
            "source_key": "TEXT",
            "is_demo": "INTEGER DEFAULT 0",
        }
        for col, sql_type in required_cols.items():
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE operational_incidents ADD COLUMN {col} {sql_type}")
        conn.commit()


def _json_safe_incident(incident: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of an incident with any embedded DataFrame swapped for plain records,
    so persisting it to SQLite as JSON round-trips cleanly (json.dumps(..., default=str)
    would otherwise stringify a DataFrame into an unparseable text blob)."""
    safe = dict(incident)
    scenario = safe.get("Scenario")
    if isinstance(scenario, dict):
        converted = dict(scenario)
        for frame_key in ("scenario_df", "staffing_df", "employee_detail_df", "traffic_df"):
            if isinstance(converted.get(frame_key), pd.DataFrame):
                converted[frame_key] = converted[frame_key].to_dict("records")
        safe["Scenario"] = converted
    return safe


def save_incident(incident: Dict[str, Any]) -> None:
    """Insert or refresh one incident while preserving any planner decision.

    Live Weather/Recall incidents now use stable source IDs, so a later refresh updates the
    same source incident rather than adding an indistinguishable duplicate row.
    """
    if incident["Type"] == "Weather":
        state = incident.get("Scenario", {}).get("state") or incident.get("Evidence", {}).get("region") or ""
        product = ""
    else:
        state = ""
        product = incident.get("Match", {}).get("matched_product") or ""
    event = str(incident.get("Event") or "")
    store_ids = ", ".join(incident.get("Store IDs", []) or [])
    run_id = str(incident.get("Run ID") or "")
    source_key = str(incident.get("Source Key") or "")
    is_demo = 1 if incident.get("Evidence", {}).get("is_demo_scenario") or "Demonstration" in str(incident.get("Source", "")) else 0
    with sqlite3.connect(settings.RUN_HISTORY_DB, timeout=10) as conn:
        conn.execute(
            """
            INSERT INTO operational_incidents (
                incident_id, created_at, incident_type, priority, status, affected_scope,
                payload_json, decision_status, state, product, event, store_ids, run_id, source_key, is_demo
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Proposed', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(incident_id) DO UPDATE SET
                decision_status=CASE WHEN operational_incidents.run_id = excluded.run_id THEN operational_incidents.decision_status ELSE 'Proposed' END,
                created_at=excluded.created_at,
                priority=excluded.priority,
                status=excluded.status,
                affected_scope=excluded.affected_scope,
                payload_json=excluded.payload_json,
                state=excluded.state,
                product=excluded.product,
                event=excluded.event,
                store_ids=excluded.store_ids,
                run_id=excluded.run_id,
                source_key=excluded.source_key,
                is_demo=excluded.is_demo
            """,
            (
                incident["Incident ID"], utc_now(), incident["Type"], incident["Priority"], incident["Status"],
                incident["Affected Scope"], json.dumps(_json_safe_incident(incident), default=str), state, product,
                event, store_ids, run_id, source_key, is_demo,
            ),
        )
        conn.commit()


def load_incidents(limit: int = 100) -> pd.DataFrame:
    """Load the operational inbox with explicit event/store context.

    Event and Store IDs are surfaced because an incident should be understandable from the
    inbox itself, without opening Impact just to discover which store the row represents.
    """
    with sqlite3.connect(settings.RUN_HISTORY_DB, timeout=10) as conn:
        return pd.read_sql_query(
            """
            SELECT incident_id AS "Incident ID", created_at AS "Created At", incident_type AS "Type",
                   priority AS "Priority", decision_status AS "Decision Status", affected_scope AS "Affected Scope",
                   COALESCE(state, '') AS "State", COALESCE(product, '') AS "Product",
                   COALESCE(event, '') AS "Event", COALESCE(store_ids, '') AS "Store IDs",
                   COALESCE(run_id, '') AS "Run ID", COALESCE(is_demo, 0) AS "Demo"
            FROM operational_incidents
            ORDER BY CASE WHEN status IN ('no_match', 'unmatched') THEN 1 ELSE 0 END ASC, created_at DESC
            LIMIT ?
            """,
            conn,
            params=(int(limit),),
        )


def load_incident_payload(incident_id: str) -> Optional[Dict[str, Any]]:
    """Load one incident's full detail back from SQLite (for incidents from a prior
    session, or a demonstration scenario, that aren't in the current run's memory)."""
    with sqlite3.connect(settings.RUN_HISTORY_DB, timeout=10) as conn:
        row = conn.execute(
            "SELECT payload_json FROM operational_incidents WHERE incident_id = ?", (incident_id,)
        ).fetchone()
    if not row or not row[0]:
        return None
    incident = json.loads(row[0])
    scenario = incident.get("Scenario")
    if isinstance(scenario, dict):
        for frame_key in ("scenario_df", "staffing_df", "employee_detail_df", "traffic_df"):
            if isinstance(scenario.get(frame_key), list):
                scenario[frame_key] = pd.DataFrame(scenario[frame_key])
    return incident


def save_decision(incident_id: str, actor: str, decision: str, reason: str, modified_action: str = "", expected_run_id: Optional[str] = None) -> None:
    if decision == "Modified" and not modified_action.strip():
        raise ValueError("Modified action is required.")
    if not reason.strip():
        raise ValueError("Reason is required.")
    snapshot = load_incident_payload(incident_id) or {}
    if expected_run_id is not None and str(snapshot.get("Run ID", "")) != expected_run_id:
        raise ValueError("A newer assessment exists. Open the latest incident before recording a decision.")
    reason = f"{reason} [Assessment run: {snapshot.get('Run ID', 'scenario')}; payload: {payload_hash(_json_safe_incident(snapshot))[:16]}]"
    with sqlite3.connect(settings.RUN_HISTORY_DB, timeout=10) as conn:
        conn.execute(
            """
            INSERT INTO operational_decisions (incident_id, created_at, actor, decision, reason, modified_action)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (incident_id, utc_now(), actor, decision, reason, modified_action),
        )
        conn.execute("UPDATE operational_incidents SET decision_status = ? WHERE incident_id = ?", (decision, incident_id))
        conn.commit()
    log_operational_event("action_decision_recorded", incident_id=incident_id, decision=decision, actor=actor)


def load_decisions(incident_id: str) -> pd.DataFrame:
    with sqlite3.connect(settings.RUN_HISTORY_DB, timeout=10) as conn:
        return pd.read_sql_query(
            """
            SELECT created_at AS "Timestamp", actor AS "Actor", decision AS "Decision",
                   reason AS "Reason", modified_action AS "Modified Action"
            FROM operational_decisions
            WHERE incident_id = ?
            ORDER BY created_at DESC
            """,
            conn,
            params=(incident_id,),
        )
