from typing import Any
from typing import Dict
import json
import pandas as pd
import sqlite3

from market_intelligence.persistence.operational_store import _json_safe_incident
from market_intelligence.signals.retail_context import top_signals_summary
from market_intelligence.signals.risk import risk_band
from market_intelligence.util.clock import utc_now

from market_intelligence.config import settings

def init_run_history_db() -> None:
    """Create the lightweight local version-history table if it does not exist."""
    with sqlite3.connect(settings.RUN_HISTORY_DB, timeout=10) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_intelligence_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_type TEXT NOT NULL,
                retailer TEXT,
                source TEXT,
                keywords TEXT,
                time_window TEXT,
                geography TEXT,
                top_signal TEXT,
                top_level TEXT,
                feature_rows INTEGER DEFAULT 0,
                feature_json TEXT,
                brief TEXT
            )
            """
        )
        # Unlike operational_incidents (which already migrates), this table only ever
        # had CREATE TABLE IF NOT EXISTS -- an old SQLite file predating a future column
        # addition here would fail every INSERT with "table has N columns but M values
        # were supplied." Bring any pre-existing DB file up to the current schema the
        # same way operational_incidents does, so this doesn't silently break for a
        # returning user's local database when a column is added later.
        existing_run_cols = {row[1] for row in conn.execute("PRAGMA table_info(market_intelligence_runs)").fetchall()}
        required_run_cols = {
            "retailer": "TEXT",
            "source": "TEXT",
            "keywords": "TEXT",
            "time_window": "TEXT",
            "geography": "TEXT",
            "top_signal": "TEXT",
            "top_level": "TEXT",
            "feature_rows": "INTEGER DEFAULT 0",
            "feature_json": "TEXT",
            "brief": "TEXT",
        }
        for col, sql_type in required_run_cols.items():
            if col not in existing_run_cols:
                conn.execute(f"ALTER TABLE market_intelligence_runs ADD COLUMN {col} {sql_type}")
        conn.commit()


def save_run_history(
    run: Dict[str, Any],
    *,
    run_type: str,
    source: str,
    keywords: str = "",
    time_window: str = "",
    geography: str = "",
) -> None:
    """Persist one completed run for local versioning without changing the active run logic."""
    with sqlite3.connect(settings.RUN_HISTORY_DB) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS run_evidence (timestamp TEXT PRIMARY KEY, payload_json TEXT NOT NULL)")
        serial = dict(run)
        serial["feature_df"] = run.get("feature_df", pd.DataFrame()).to_dict("records")
        serial["results"] = {key: {k: (v.to_dict("records") if isinstance(v, pd.DataFrame) else v) for k,v in value.items()} for key,value in run.get("results", {}).items()}
        serial["operational_incidents_snapshot"] = [_json_safe_incident(i) for i in run.get("operational_incidents_snapshot", [])]
        connection.execute("INSERT OR REPLACE INTO run_evidence VALUES (?, ?)", (str(run.get("timestamp")), json.dumps(serial, default=str)))
    feature_df = run.get("feature_df", pd.DataFrame())
    if feature_df is None:
        feature_df = pd.DataFrame()
    top_signal = "No signal"
    top_level = "Low"
    if not feature_df.empty and "risk_score" in feature_df.columns:
        # Same rule as the Results header: every signal tied at the top level, with its state.
        top_signal, top_level = top_signals_summary(feature_df)
    config = run.get("run_config", {})
    with sqlite3.connect(settings.RUN_HISTORY_DB, timeout=10) as conn:
        conn.execute(
            """
            INSERT INTO market_intelligence_runs (
                created_at, run_type, retailer, source, keywords, time_window,
                geography, top_signal, top_level, feature_rows, feature_json, brief
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run.get("timestamp", utc_now())),
                run_type,
                str(config.get("retailer", "")),
                source,
                keywords,
                time_window,
                geography,
                top_signal,
                top_level,
                int(len(feature_df)),
                json.dumps(feature_df.to_dict(orient="records"), default=str),
                str(run.get("brief", "")),
            ),
        )
        conn.commit()


def load_run_history(limit: int = 10) -> pd.DataFrame:
    """Return recent local versions for display in the Signal Search page."""
    with sqlite3.connect(settings.RUN_HISTORY_DB, timeout=10) as conn:
        return pd.read_sql_query(
            """
            SELECT
                run_id AS "Run ID",
                created_at AS "Run Time",
                run_type AS "Run Type",
                source AS "Source",
                keywords AS "Keywords",
                time_window AS "Time Window",
                geography AS "Geography",
                top_signal AS "Top Signal",
                top_level AS "Level",
                feature_rows AS "Feature Rows"
            FROM market_intelligence_runs
            ORDER BY run_id DESC
            LIMIT ?
            """,
            conn,
            params=(int(limit),),
        )
