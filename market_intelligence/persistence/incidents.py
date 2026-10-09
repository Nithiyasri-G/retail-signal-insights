from __future__ import annotations

import sqlite3
from pathlib import Path

from market_intelligence.models.incidents import Incident


class IncidentRepository:
    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS typed_incidents (incident_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)"
            )

    def save(self, incident: Incident) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO typed_incidents VALUES (?, ?)",
                (incident.incident_id, incident.model_dump_json()),
            )

    def get(self, incident_id: str) -> Incident | None:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT payload_json FROM typed_incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
        return Incident.model_validate_json(row[0]) if row else None
