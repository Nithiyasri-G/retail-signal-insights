from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class ScenarioRecord:
    scenario_id: str
    scenario_type: str
    assumption_version: str
    created_by: str
    created_at: str
    assumptions: dict[str, Any]
    result: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        scenario_type: str,
        created_by: str,
        assumptions: Mapping[str, Any],
        result: Mapping[str, Any],
        assumption_version: str = "scenario-assumptions-v1",
    ) -> "ScenarioRecord":
        return cls(
            scenario_id=f"SCN-{uuid.uuid4().hex[:10].upper()}",
            scenario_type=scenario_type,
            assumption_version=assumption_version,
            created_by=created_by or "POC User",
            created_at=datetime.now(timezone.utc).isoformat(),
            assumptions=dict(assumptions),
            result=dict(result),
        )


def _initialize(database: str | Path) -> None:
    with sqlite3.connect(str(database)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_runs (
                scenario_id TEXT PRIMARY KEY,
                scenario_type TEXT NOT NULL,
                assumption_version TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                assumptions_json TEXT NOT NULL,
                result_json TEXT NOT NULL
            )
            """
        )


def save_scenario(database: str | Path, record: ScenarioRecord) -> None:
    _initialize(database)
    with sqlite3.connect(str(database)) as connection:
        connection.execute(
            """
            INSERT INTO scenario_runs (
                scenario_id, scenario_type, assumption_version, created_by,
                created_at, assumptions_json, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.scenario_id,
                record.scenario_type,
                record.assumption_version,
                record.created_by,
                record.created_at,
                json.dumps(record.assumptions, default=str),
                json.dumps(record.result, default=str),
            ),
        )


def list_scenarios(database: str | Path, limit: int = 50) -> list[ScenarioRecord]:
    _initialize(database)
    with sqlite3.connect(str(database)) as connection:
        rows = connection.execute(
            """
            SELECT scenario_id, scenario_type, assumption_version, created_by,
                   created_at, assumptions_json, result_json
            FROM scenario_runs ORDER BY created_at DESC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [
        ScenarioRecord(
            scenario_id=row[0],
            scenario_type=row[1],
            assumption_version=row[2],
            created_by=row[3],
            created_at=row[4],
            assumptions=json.loads(row[5]),
            result=json.loads(row[6]),
        )
        for row in rows
    ]
