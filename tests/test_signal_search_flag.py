"""Signal Search needs an Apify account, so it is hidden unless ENABLE_SIGNAL_SEARCH is set."""
import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _labels(app: AppTest) -> list[str]:
    return [widget.label for widget in app.text_input]


def test_signal_search_and_apify_token_are_hidden_by_default(isolated_database: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_SIGNAL_SEARCH", raising=False)
    os.environ["MARKET_INTELLIGENCE_DB"] = str(isolated_database)
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    assert not app.exception
    assert "Apify token" not in _labels(app)
    # A stale selection of the hidden view falls back to the first page instead of rendering it.
    app.session_state["workbench_view"] = "Signal Search"
    app.run()
    assert not app.exception
    assert app.session_state["workbench_view"] == "Configure"


def test_signal_search_returns_when_enabled(isolated_database: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_SIGNAL_SEARCH", "true")
    os.environ["MARKET_INTELLIGENCE_DB"] = str(isolated_database)
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    assert "Apify token" in _labels(app)
    app.session_state["workbench_view"] = "Signal Search"
    app.run()
    assert not app.exception
    assert app.session_state["workbench_view"] == "Signal Search"
