"""Retail Signal Intelligence -- Streamlit entry point.

This file only wires the page together: page setup, the shared per-run context, the sidebar and
header, and routing to one view function per workbench screen. All business logic lives in the
`market_intelligence` package (config, infra, collectors, signals, weather, recalls, demand,
incidents, llm, persistence, application) and all rendering in `market_intelligence.ui`.
"""
from market_intelligence.ui.bootstrap import init_app_state
from market_intelligence.ui.context import ScriptContext
from market_intelligence.ui.footer import render_footer
from market_intelligence.ui.layout import render_header_and_navigation
from market_intelligence.ui.sidebar import render_sidebar
from market_intelligence.ui.theme import configure_page, inject_css
from market_intelligence.ui.views.configure import render_configure
from market_intelligence.ui.views.credentials import render_credential_checks
from market_intelligence.ui.views.demand_planner import render_demand_planner_view
from market_intelligence.ui.views.evidence_page import render_evidence_page
from market_intelligence.ui.views.operational_impact.center import render_operational_impact_center
from market_intelligence.ui.views.raw_data import render_raw_data
from market_intelligence.ui.views.results_page import render_results_page
from market_intelligence.ui.views.run_pipeline import run_intelligence_pipeline
from market_intelligence.ui.views.run_restore import render_previous_run_status
from market_intelligence.ui.views.signal_search import render_signal_search

configure_page()
inject_css()

ctx = ScriptContext()
init_app_state(ctx)
render_sidebar(ctx)
render_header_and_navigation(ctx)
render_previous_run_status(ctx)

if ctx.view == "Signal Search":
    render_signal_search(ctx)

if ctx.view == "Demand Planner":
    render_demand_planner_view(ctx.nvidia_key, ctx.nvidia_model)

if ctx.view == "Operational Impact Center":
    render_operational_impact_center(ctx.nvidia_key, ctx.nvidia_model)

if ctx.view == "Configure":
    render_configure(ctx)

render_credential_checks(ctx)

if ctx.run_button:
    run_intelligence_pipeline(ctx)

if ctx.view == "Results":
    render_results_page(ctx)

if ctx.view == "Evidence Audit":
    render_evidence_page(ctx)

if ctx.view == "Raw Data":
    render_raw_data(ctx)

render_footer(ctx)
