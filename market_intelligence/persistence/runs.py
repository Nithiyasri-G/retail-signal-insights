from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping


class RunRepository:
    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS intelligence_runs (run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload_json TEXT NOT NULL)"
            )

    def save(self, run_id: str, created_at: str, payload: Mapping[str, Any]) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO intelligence_runs VALUES (?, ?, ?)",
                (run_id, created_at, json.dumps(payload, default=str)),
            )
