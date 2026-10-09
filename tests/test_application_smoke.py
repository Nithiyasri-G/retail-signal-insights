from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "app.py"
PRODUCTION_DATABASE = PROJECT_ROOT / "market_intelligence_runs.db"


def _sha256(path: Path) -> str | None:
    """Return a digest without requiring a production database to exist."""
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_application_uses_configured_database_without_touching_source_database(
    isolated_database: Path,
) -> None:
    """Catches a regression where tests or deployments always write beside app.py."""
    before = _sha256(PRODUCTION_DATABASE)
    env = os.environ.copy()
    env["MARKET_INTELLIGENCE_DB"] = str(isolated_database)

    result = subprocess.run(
        [sys.executable, "-c", "import runpy; runpy.run_path('app.py')"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert isolated_database.exists()
    with sqlite3.connect(isolated_database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "market_intelligence_runs",
        "operational_incidents",
        "operational_decisions",
    }.issubset(tables)
    assert _sha256(PRODUCTION_DATABASE) == before


def test_nvidia_key_field_never_prefills_from_environment_variable(
    isolated_database: Path, monkeypatch
) -> None:
    """U01: the NVIDIA key field previously defaulted to
    value=os.getenv("NVIDIA_API_KEY", ""), which put a server-configured secret
    straight into the rendered page even though the user never typed it --
    type="password" only masks the on-screen display, it does not stop the value from
    being present in the page/session. The field must render empty regardless of
    whether a server key is configured; a server-configured key is still used when the
    field is left blank (confirmed via the existing "Configured this session" caption).
    """
    monkeypatch.setenv("NVIDIA_API_KEY", "sk-a-real-server-secret-should-not-render")
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.run()
    assert not app.exception

    nvidia_field = next(w for w in app.text_input if w.label == "LLM API key")
    assert nvidia_field.value == ""

    captions = [c.value for c in app.caption]
    assert any("Configured this session: LLM" in c for c in captions)


def test_run_intelligence_button_runs_the_headless_use_case_and_opens_results(
    isolated_database: Path, monkeypatch
) -> None:
    """The sidebar button drives application.run_intelligence through the Streamlit observer:
    the finished run lands in session state and the app switches to the Results view."""
    from market_intelligence.application import run_intelligence as ri

    row = {"source": "BLS CPI", "signal_area": "Inflation", "signal_name": "inflation_pressure_score",
           "risk_score": 4.0, "score_reason": "r", "region": "US"}
    monkeypatch.setattr(ri, "collect_gnews", lambda *a, **k: {"status": "success", "rows": [row], "items": [{"title": "t"}]})
    monkeypatch.setattr(ri, "collect_bls_cpi", lambda *a, **k: {"status": "success", "rows": [row], "items": []})
    monkeypatch.setattr(ri, "collect_fda_recalls", lambda *a, **k: {"status": "empty", "rows": [], "items": []})
    monkeypatch.setattr(ri, "collect_weather_alerts_multi", lambda *a, **k: {"status": "empty", "rows": [], "items": []})

    app = AppTest.from_file(str(APP_PATH), default_timeout=60)
    app.run()
    assert not app.exception
    run_button = next(b for b in app.sidebar.button if b.label == "Run intelligence")
    run_button.click().run()
    assert not app.exception, app.exception
    run = app.session_state["run"]
    assert run["brief_source"] == "fallback" and len(run["feature_df"]) >= 2
    assert app.session_state["workbench_view"] == "Results"
    assert app.session_state["last_collector_states"]["BRIEF"]["status"] == "skipped"


def test_all_workbench_views_render_without_uncaught_exception(
    isolated_database: Path,
) -> None:
    """Catches routing or import failures on any top-level workbench view."""
    os.environ["MARKET_INTELLIGENCE_DB"] = str(isolated_database)
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.run()
    assert not app.exception
    assert "Include saved search demand" not in [widget.label for widget in app.checkbox]

    views = [
        "Configure",
        "Demand Planner",
        "Operational Impact Center",
        "Results",
        "Evidence Audit",
        "Raw Data",
    ]
    for view in views:
        # Streamlit 1.56 exposes segmented controls in the runtime but not in
        # AppTest's element collection; use the equivalent widget when running
        # against that test API so the smoke test still exercises every route.
        if app.button_group:
            app.button_group[0].set_value(view).run()
        elif hasattr(app, "segmented_control") and app.segmented_control:
            app.segmented_control[0].set_value(view).run()
        elif app.radio:
            app.radio[0].set_value(view).run()
        else:
            raise AssertionError("Navigation widget was not rendered")
        assert not app.exception, f"{view}: {app.exception}"
