from html import escape
from typing import Any
from typing import Dict
import pandas as pd
import streamlit as st

from market_intelligence.llm.audit import display_fallback_reason


def render_audit_command_header(
    run: Dict[str, Any],
    mock_used: bool,
    raw_records: int,
    pulled_sources: int,
    llm_audit: Dict[str, Any],
    feature_df: pd.DataFrame,
) -> None:
    brief_mode = "Agent" if run.get("brief_source") == "nvidia" else "Rule-based"
    cells = [
        ("Mock Data", "External signals live" if not mock_used else "Review provenance"),
        ("Raw Records", str(raw_records)),
        ("Live Sources", str(pulled_sources)),
        ("Brief Mode", brief_mode),
    ]
    cell_html = "".join(
        f"<div class='audit-status-cell'><div class='audit-status-label'>{escape(label)}</div><div class='audit-status-value'>{escape(value)}</div></div>"
        for label, value in cells
    )
    status_copy = (
        "All generated rows are traceable to collector outputs and the brief is tied to the shown payload."
        if not mock_used
        else "At least one collector or analysis step is marked as mock. Review provenance before using this run."
    )
    if llm_audit.get("fallback_used"):
        status_copy += f" {display_fallback_reason(llm_audit.get('fallback_reason', ''))}"
    st.markdown(
        "<div class='audit-command'>"
        "<div>"
        "<div class='config-eyebrow'>Evidence Audit</div>"
        "<div class='audit-command-title'>Run provenance and chain of custody</div>"
        f"<div class='audit-command-copy'>{escape(status_copy)} Feature rows available: {len(feature_df)}.</div>"
        "</div>"
        f"<div class='audit-status-grid'>{cell_html}</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def render_audit_lineage() -> None:
    steps = [
        ("01", "Request", "Run config stores source toggles, query scope, limits, and guarded Apify mode."),
        ("02", "Collect", "Each collector records status, endpoint/actor, raw record count, and errors."),
        ("03", "Normalize", "Collector rows become normalized external signals with level reasoning and raw references."),
        ("04", "Analyze", "The summary input is fingerprinted and capped; who wrote the summary is recorded separately."),
        ("05", "Inspect", "Normalized rows, brief trace, and output can be reviewed here; raw payloads are in Raw Data."),
    ]
    html = "".join(
        "<div class='audit-lineage-step'>"
        f"<div class='audit-lineage-num'>{escape(num)}</div>"
        f"<div class='audit-lineage-title'>{escape(title)}</div>"
        f"<div class='audit-lineage-copy'>{escape(copy)}</div>"
        "</div>"
        for num, title, copy in steps
    )
    st.markdown("<div class='audit-lineage'>" + html + "</div>", unsafe_allow_html=True)
