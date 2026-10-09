from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    database = tmp_path / "test_runs.db"
    monkeypatch.setenv("MARKET_INTELLIGENCE_DB", str(database))
    return database


@pytest.fixture(autouse=True)
def clear_external_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("NVIDIA_API_KEY", "APIFY_API_TOKEN", "BLS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
