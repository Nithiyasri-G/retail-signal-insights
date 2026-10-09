"""The Run Intelligence use case runs headless (no Streamlit) and reports through an observer."""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("MARKET_INTELLIGENCE_DB", tempfile.mktemp(suffix=".db"))

from market_intelligence.application import run_intelligence as ri
from market_intelligence.application.run_intelligence import RunRequest, run_intelligence


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def progress(self, title, collector_states, pct, sidebar_message=None, sidebar_level="info") -> None:
        self.events.append(("progress", title, pct, sidebar_message, sidebar_level,
                            {k: v["status"] for k, v in collector_states.items()}))

    def brief_ready(self) -> None:
        self.events.append(("brief_ready",))

    def run_ready(self, completed_run) -> None:
        self.events.append(("run_ready", sorted(completed_run)))


def _request(**overrides) -> RunRequest:
    base = dict(
        retailer_label="Dollar Tree", region="US", country="US", language="en",
        use_gnews=True, use_bls=True, use_fda=False, use_weather=False,
        bls_key="", nvidia_key="", nvidia_model="m", news_keywords=["batteries"], trends_keywords=["a", "b", "c"],
        gnews_period="7d", max_news=24, fda_query="q", fda_limit=20, weather_areas=["TX"], weather_limit=5,
        apify_requested=False, apify_time_range="now 7-d", apify_geo="US", run_config={"retailer": "Dollar Tree"},
    )
    base.update(overrides)
    return RunRequest(**base)


def _stub_pipeline(monkeypatch, saved):
    row = {"source": "BLS CPI", "signal_area": "Inflation", "signal_name": "inflation_pressure_score",
           "risk_score": 4.0, "score_reason": "r", "region": "US"}
    monkeypatch.setattr(ri, "collect_gnews", lambda *a, **k: {"status": "success", "rows": [row], "items": [{"title": "t"}]})
    monkeypatch.setattr(ri, "collect_bls_cpi", lambda *a, **k: {"status": "success", "rows": [row], "items": []})
    monkeypatch.setattr(ri, "generate_nvidia_brief", lambda *a, **k: ("brief text", "fallback", {"fallback_reason": "no key"}))
    monkeypatch.setattr(ri, "save_run_history", lambda run, **kw: saved.append((run, kw)))
    monkeypatch.setattr(ri, "save_incident", lambda incident: saved.append(("incident", incident)))


def test_headless_run_reports_progress_in_order_and_persists(monkeypatch) -> None:
    saved: list = []
    _stub_pipeline(monkeypatch, saved)
    observer = Recorder()
    outcome = run_intelligence(_request(), observer)

    titles = [e[1] for e in observer.events if e[0] == "progress"]
    assert titles == [
        "Starting collectors", "Collecting retail news", "Retail news complete",
        "Collecting CPI inflation", "CPI collection complete", "Generating intelligence brief", "Run complete",
    ]
    kinds = [e[0] for e in observer.events]
    # The final progress call is followed by brief_ready, then run_ready (the hand-off point
    # where the front end publishes the finished run, before run history is written).
    assert kinds[-3:] == ["progress", "brief_ready", "run_ready"]
    assert observer.events[-3][1] == "Run complete"
    # Sources the user did not enable are "disabled", not "skipped" (see run_progress_pct).
    final_states = outcome.collector_states
    assert final_states["FDA"]["status"] == "disabled" and final_states["WEATHER"]["status"] == "disabled"
    assert final_states["GNEWS"]["status"] == "success" and final_states["BLS"]["status"] == "success"
    assert outcome.completed_run["brief_source"] == "fallback"
    assert len(outcome.completed_run["feature_df"]) == 2
    (run, kwargs), = saved
    assert kwargs["run_type"] == "Full Intelligence Run" and kwargs["keywords"] == "" and kwargs["geography"] == "US"


def test_a_collector_exception_is_isolated_and_the_run_still_completes(monkeypatch) -> None:
    saved: list = []
    _stub_pipeline(monkeypatch, saved)

    def boom(*args, **kwargs):
        raise RuntimeError("malformed payload")

    monkeypatch.setattr(ri, "collect_bls_cpi", boom)
    outcome = run_intelligence(_request(), Recorder())
    assert outcome.collector_states["BLS"]["status"] == "failed"
    assert "malformed payload" in outcome.collector_states["BLS"]["detail"]
    assert outcome.collector_states["GNEWS"]["status"] == "success"
    assert outcome.completed_run["brief"] == "brief text"


def test_a_failed_run_history_write_does_not_abort_the_use_case(monkeypatch) -> None:
    saved: list = []
    _stub_pipeline(monkeypatch, saved)

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(ri, "save_run_history", broken)
    outcome = run_intelligence(_request(), Recorder())
    assert outcome.incident_count == 0 and outcome.completed_run["brief"] == "brief text"
