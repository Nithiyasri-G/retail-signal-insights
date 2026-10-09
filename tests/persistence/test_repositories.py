import sqlite3
from datetime import datetime, timezone

from market_intelligence.models.incidents import DataMode, Incident, ProvenanceRecord
from market_intelligence.persistence.incidents import IncidentRepository
from market_intelligence.persistence.runs import RunRepository


NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def test_run_and_typed_incident_repositories_round_trip(tmp_path) -> None:
    database = tmp_path / "repo.db"
    RunRepository(database).save("RUN-1", NOW.isoformat(), {"status": "ok"})
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT run_id FROM intelligence_runs").fetchone()[0] == "RUN-1"

    repository = IncidentRepository(database)
    incident = Incident(
        incident_id="WX-1", incident_type="Weather", source_record_id="NWS-1",
        event="Flood Watch", status="ok", created_at=NOW,
        provenance=ProvenanceRecord(
            external_signal=DataMode.LIVE, internal_operations=DataMode.SYNTHETIC,
            explanation=DataMode.CALCULATED, dataset_version="fixtures-v1", as_of=NOW,
        ),
    )
    repository.save(incident)
    assert repository.get("WX-1") == incident
    assert repository.get("missing") is None
