"""Run Intelligence use case: collect the enabled sources, build the feature table and brief,
derive operational incidents and persist the run. No Streamlit here -- progress and the hand-off
of the finished run go through a RunObserver so any front end (or a test) can drive it."""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

import pandas as pd

from market_intelligence.application.run_status import brief_pipeline_state, run_progress_pct
from market_intelligence.collectors.live_bls import collect_bls_cpi
from market_intelligence.collectors.live_fda import collect_fda_recalls
from market_intelligence.collectors.live_news import collect_gnews
from market_intelligence.collectors.live_noaa import collect_weather_alerts_multi
from market_intelligence.config.logging_setup import log_operational_event
from market_intelligence.config.settings import APIFY_HARD_KEYWORD_LIMIT
from market_intelligence.data.demo import demo_store_master
from market_intelligence.incidents.builders import build_recall_incidents, build_weather_incidents
from market_intelligence.llm.brief import generate_nvidia_brief
from market_intelligence.persistence.operational_store import save_incident
from market_intelligence.persistence.run_history import save_run_history
from market_intelligence.signals.retail_context import enrich_feature_rows_for_retailer
from market_intelligence.util.clock import utc_now


class RunObserver(Protocol):
    def progress(
        self,
        title: str,
        collector_states: Dict[str, Dict[str, str]],
        pct: int,
        sidebar_message: Optional[str] = None,
        sidebar_level: str = "info",
    ) -> None: ...

    def brief_ready(self) -> None:
        """The brief step finished; called before the finished run is assembled."""

    def run_ready(self, completed_run: Dict[str, Any]) -> None:
        """The finished run exists and its incidents are saved; run history is saved next."""


@dataclass
class RunRequest:
    retailer_label: str
    region: str
    country: str
    language: str
    use_gnews: bool
    use_bls: bool
    use_fda: bool
    use_weather: bool
    bls_key: str
    nvidia_key: str
    nvidia_model: str
    news_keywords: List[str]
    trends_keywords: List[str]
    gnews_period: str
    max_news: int
    fda_query: str
    fda_limit: int
    weather_areas: List[str]
    weather_limit: int
    apify_requested: bool
    apify_time_range: str
    apify_geo: str
    run_config: Dict[str, Any]


@dataclass
class RunOutcome:
    completed_run: Dict[str, Any]
    collector_states: Dict[str, Dict[str, str]]
    incident_count: int


def run_intelligence(request: RunRequest, observer: RunObserver) -> RunOutcome:
    retailer_label = request.retailer_label
    region = request.region
    country = request.country
    language = request.language
    use_gnews, use_bls, use_fda, use_weather = request.use_gnews, request.use_bls, request.use_fda, request.use_weather
    log_operational_event("run_started", retailer=retailer_label, region=region)
    run_config = request.run_config
    results: Dict[str, Dict[str, Any]] = {}
    all_rows: List[Dict[str, Any]] = []
    all_articles: List[Dict[str, Any]] = []

    steps = [
        ("gnews", use_gnews),
        ("bls", use_bls),
        ("fda", use_fda),
        ("weather", use_weather),
    ]
    active_steps = [step for step in steps if step[1]]
    total = max(1, len(active_steps))
    completed = 0
    # A user-unchecked source must use the "disabled" status, not "skipped" --
    # run_progress_pct() already excludes "disabled" from its denominator (a source the
    # user never asked for isn't an incomplete step), while "skipped" counts toward the
    # denominator WITHOUT counting as a success (that status is reserved for the brief
    # step's local-fallback outcome below, a step that did run but didn't succeed via
    # NVIDIA). Reusing "skipped" for both meanings made a fully successful run with one
    # source unchecked show below 100% at completion.
    collector_states: Dict[str, Dict[str, str]] = {
        "GNEWS": {"status": "queued" if use_gnews else "disabled", "detail": "Retail news collector" if use_gnews else "Disabled"},
        "BLS": {"status": "queued" if use_bls else "disabled", "detail": "CPI collector" if use_bls else "Disabled"},
        "FDA": {"status": "queued" if use_fda else "disabled", "detail": "Recall collector" if use_fda else "Disabled"},
        "WEATHER": {"status": "queued" if use_weather else "disabled", "detail": "NOAA alert collector" if use_weather else "Disabled"},
        "BRIEF": {"status": "queued", "detail": "Agent-written brief or rule-based summary"},
    }
    observer.progress("Starting collectors", collector_states, 2, "Running: starting collectors...", "info")

    if use_gnews:
        collector_states["GNEWS"] = {"status": "running", "detail": "Collecting and deduplicating retail news"}
        observer.progress("Collecting retail news", collector_states, int((completed / total) * 100), "Running: collecting retail news...", "info")
        try:
            results["gnews"] = collect_gnews(
                request.news_keywords,
                country,
                language,
                request.gnews_period,
                request.max_news,
                retailer_label,
            )
        except Exception as exc:
            log_operational_event("source_failed", source="gnews", error=str(exc))
            results["gnews"] = {"status": "failed", "source": "GNews", "error": f"Unexpected GNews collector error: {exc}", "raw": [], "rows": [], "items": []}
        all_rows.extend(results["gnews"].get("rows", []))
        all_articles.extend(results["gnews"].get("items", []))
        completed += 1
        gnews_status = results["gnews"].get("status", "failed")
        collector_states["GNEWS"] = {
            "status": "success" if gnews_status == "success" else "failed" if gnews_status == "failed" else "skipped",
            "detail": results["gnews"].get("error") or f"{len(results['gnews'].get('items', []))} article(s) aggregated into {len(results['gnews'].get('rows', []))} signal row(s)",
        }
        observer.progress(
            "Retail news complete", collector_states, int((completed / total) * 100),
            f"GNews {collector_states['GNEWS']['status']}: {collector_states['GNEWS']['detail']}",
            "warning" if gnews_status != "success" else "info",
        )

    if use_bls:
        collector_states["BLS"] = {"status": "running", "detail": "Collecting headline and category CPI"}
        observer.progress("Collecting CPI inflation", collector_states, int((completed / total) * 100), "Running: collecting BLS CPI...", "info")
        try:
            results["bls"] = collect_bls_cpi(request.bls_key.strip(), retailer_label)
        except Exception as exc:
            log_operational_event("source_failed", source="bls", error=str(exc))
            results["bls"] = {"status": "failed", "source": "BLS CPI", "error": f"Unexpected BLS collector error: {exc}", "rows": [], "items": []}
        all_rows.extend(results["bls"].get("rows", []))
        completed += 1
        bls_status = results["bls"].get("status", "failed")
        collector_states["BLS"] = {
            "status": "success" if bls_status == "success" else "failed",
            "detail": results["bls"].get("error") or f"{len(results['bls'].get('rows', []))} CPI signal row(s)",
        }
        observer.progress(
            "CPI collection complete", collector_states, int((completed / total) * 100),
            f"BLS {collector_states['BLS']['status']}: {collector_states['BLS']['detail']}",
            "warning" if bls_status != "success" else "info",
        )

    if use_fda:
        collector_states["FDA"] = {"status": "running", "detail": "Collecting food recall records"}
        observer.progress("Collecting FDA recalls", collector_states, int((completed / total) * 100), "Running: collecting FDA recalls...", "info")
        try:
            results["fda"] = collect_fda_recalls(
                request.fda_query,
                request.fda_limit,
                retailer_label,
            )
        except Exception as exc:
            log_operational_event("source_failed", source="fda", error=str(exc))
            results["fda"] = {"status": "failed", "source": "openFDA Food Enforcement", "error": f"Unexpected openFDA collector error: {exc}", "rows": [], "items": []}
        all_rows.extend(results["fda"].get("rows", []))
        completed += 1
        fda_status = results["fda"].get("status", "failed")
        collector_states["FDA"] = {
            "status": "success" if fda_status == "success" else "failed",
            "detail": results["fda"].get("error") or f"{len(results['fda'].get('items', []))} recall item(s), {len(results['fda'].get('rows', []))} signal row(s)",
        }
        observer.progress(
            "FDA recall collection complete", collector_states, int((completed / total) * 100),
            f"FDA {collector_states['FDA']['status']}: {collector_states['FDA']['detail']}",
            "warning" if fda_status != "success" else "info",
        )

    if use_weather:
        weather_areas = request.weather_areas
        weather_area = ", ".join(weather_areas)
        collector_states["WEATHER"] = {"status": "running", "detail": f"Collecting active NOAA alerts for {weather_area}"}
        observer.progress(
            "Collecting weather alerts", collector_states, int((completed / total) * 100),
            f"Running: collecting NOAA weather alerts for {weather_area}...", "info",
        )
        try:
            results["weather"] = collect_weather_alerts_multi(
                weather_areas,
                request.weather_limit,
                retailer_label,
                store_master=demo_store_master(),
            )
        except Exception as exc:
            log_operational_event("source_failed", source="weather", error=str(exc))
            results["weather"] = {"status": "failed", "source": "NOAA Weather Alerts", "error": f"Unexpected NOAA collector error: {exc}", "rows": [], "items": []}
        all_rows.extend(results["weather"].get("rows", []))
        completed += 1
        weather_status = results["weather"].get("status", "failed")
        collector_states["WEATHER"] = {
            "status": "success" if weather_status == "success" else "failed",
            "detail": results["weather"].get("error") or f"{len(results['weather'].get('items', []))} active alert item(s), {len(results['weather'].get('rows', []))} signal row(s)",
        }
        observer.progress(
            "Weather alert collection complete", collector_states, int((completed / total) * 100),
            f"Weather {collector_states['WEATHER']['status']}: {collector_states['WEATHER']['detail']}",
            "warning" if weather_status != "success" else "info",
        )

    collector_states["BRIEF"] = {"status": "running", "detail": "Briefing Agent is writing the brief; a rule-based summary is ready as backup"}
    observer.progress("Generating intelligence brief", collector_states, 98, "Running: generating executive brief...", "info")
    feature_df = pd.DataFrame(all_rows)
    if not feature_df.empty:
        feature_df["retailer"] = retailer_label
        if region:
            feature_df["selected_market"] = region
        feature_df = enrich_feature_rows_for_retailer(feature_df, retailer_label)
    brief, brief_source, llm_audit = generate_nvidia_brief(
        request.nvidia_key.strip(), request.nvidia_model.strip(), feature_df, all_articles, retailer_label, region
    )
    # A green SUCCESS badge above the word "failed" is the first thing on screen and it
    # contradicts its own body text. The brief step reports what actually happened.
    collector_states["BRIEF"] = brief_pipeline_state(brief_source, llm_audit)
    observer.progress("Run complete", collector_states, run_progress_pct(collector_states))
    observer.brief_ready()

    completed_run = {
        "timestamp": utc_now(),
        "run_config": run_config,
        "results": results,
        "feature_df": feature_df,
        "articles": all_articles,
        "brief": brief,
        "brief_source": brief_source,
        "llm_audit": llm_audit,
    }
    snapshot = []
    if results.get("weather", {}).get("status") == "success":
        snapshot.extend(build_weather_incidents(results["weather"], completed_run["timestamp"]))
    if results.get("fda", {}).get("status") == "success":
        snapshot.extend(build_recall_incidents(results["fda"], completed_run["timestamp"]))
    completed_run["operational_incidents_snapshot"] = snapshot
    for incident in snapshot:
        try:
            save_incident(incident)
            log_operational_event(
                "incident_created",
                incident_id=incident.get("Incident ID", ""),
                type=incident.get("Type", ""),
                priority=incident.get("Priority", "") or "(blank)",
            )
        except Exception as exc:
            log_operational_event(
                "persistence_error", level="error",
                incident_id=incident.get("Incident ID", ""), error=str(exc),
            )
    observer.run_ready(completed_run)
    try:
        save_run_history(
            completed_run,
            run_type="Full Intelligence Run",
            source="Configured Sources",
            keywords=", ".join(request.trends_keywords[:APIFY_HARD_KEYWORD_LIMIT]) if request.apify_requested else "",
            time_window=request.apify_time_range if request.apify_requested else "",
            geography=request.apify_geo if request.apify_requested else region,
        )
    except Exception as exc:
        log_operational_event("persistence_error", level="error", stage="save_run_history", error=str(exc))
    log_operational_event("run_completed", incident_count=len(snapshot))
    return RunOutcome(completed_run=completed_run, collector_states=collector_states, incident_count=len(snapshot))
