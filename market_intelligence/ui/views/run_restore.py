import streamlit as st

from market_intelligence.application.run_status import brief_pipeline_state, run_progress_pct
from market_intelligence.ui.components import render_run_monitor, render_sidebar_status
from market_intelligence.util.tables import format_timestamp


def render_previous_run_status(ctx) -> None:
    run_button = ctx.run_button
    run_status_slot = ctx.run_status_slot
    sidebar_status_slot = ctx.sidebar_status_slot
    if not run_button and st.session_state.get("run"):
        last_run = st.session_state["run"]
        last_states = {}
        for key, result in last_run.get("results", {}).items():
            if key == "apify":
                continue  # Do not display a saved-search status in the main run monitor.
            status = result.get("status", "unknown")
            last_states[key.upper()] = {
                "status": status if status in {"success", "failed", "skipped"} else "queued",
                "detail": result.get("error") or f"{len(result.get('rows', []))} signal row(s)",
            }
        last_llm_audit = last_run.get("llm_audit", {})
        if last_llm_audit:
            last_states["BRIEF"] = brief_pipeline_state(
                str(last_run.get("brief_source", "")), last_llm_audit
            )
        if last_states and ctx.view == "Configure":
            render_run_monitor(
                run_status_slot,
                f"Last run completed {format_timestamp(last_run.get('timestamp', ''))}",
                last_states,
                run_progress_pct(last_states),
            )
        render_sidebar_status(
            sidebar_status_slot,
            f"Last run complete: {format_timestamp(last_run.get('timestamp', ''))}",
            "success",
        )
    elif not run_button:
        render_sidebar_status(sidebar_status_slot, "Status: idle. Select sources and run intelligence.", "info")
