from html import escape
from typing import Any
from typing import Dict
from typing import List
import pandas as pd
import re
import streamlit as st

from market_intelligence.llm.audit import display_fallback_reason
from market_intelligence.llm.brief import generate_nvidia_brief
from market_intelligence.persistence.run_history import save_run_history
from market_intelligence.signals.retail_context import top_signal_labels
from market_intelligence.signals.risk import risk_band
from market_intelligence.util.clock import utc_now


BRIEF_SECTION_TITLES = {
    "executive summary",
    "top 3 insights",
    "top three insights",
    "top insights",  # L02: the model also writes this bare variant
    "top signal evidence",
    "forecasting relevance",
    "planning relevance",  # L02: the model also writes this variant
    "recommended actions",
    "confidence and limitations",
    "confidence limitations",
}


def normalize_brief_line(line: str) -> str:
    normalized = str(line or "").strip()
    normalized = re.sub(r"^\s*#{1,6}\s*", "", normalized)
    normalized = re.sub(r"^\*\*(.*?)\*\*$", r"\1", normalized)
    normalized = normalized.replace("**", "")
    return normalized.strip()


def clean_brief_heading(line: str) -> str:
    heading = normalize_brief_line(line).rstrip(":").strip()
    heading = re.sub(r"^\d+[\.)]\s*", "", heading).strip()
    return heading


def brief_section_heading(raw_line: str) -> str:
    stripped = str(raw_line or "").strip()
    if re.fullmatch(r"[-*_]{3,}", stripped):
        return ""
    normalized = normalize_brief_line(stripped)
    title = clean_brief_heading(normalized)
    simplified = re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()
    if stripped.startswith("#") or (normalized.endswith(":") and len(normalized) <= 90):
        return title
    if simplified in BRIEF_SECTION_TITLES:
        return title
    return ""


def brief_to_html(brief: str) -> str:
    parts: List[str] = []
    in_list = False
    for raw_line in str(brief or "").splitlines():
        line = raw_line.strip()
        if not line or re.fullmatch(r"[-*_]{3,}", line):
            if in_list:
                parts.append("</ul>")
                in_list = False
            continue
        normalized = normalize_brief_line(line)
        heading = brief_section_heading(line)
        bullet_match = re.match(r"^(?:[-*]\s+|\d+\.\s+|[A-Z]\.\s+)(.*)$", normalized)
        if heading:
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<div class='brief-section-title'>{escape(heading)}</div>")
        elif bullet_match:
            if not in_list:
                parts.append("<ul class='brief-list'>")
                in_list = True
            parts.append(f"<li>{escape(bullet_match.group(1))}</li>")
        else:
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<p>{escape(normalized)}</p>")
    if in_list:
        parts.append("</ul>")
    return "".join(parts)


def parse_brief_sections(brief: str) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    current = {"title": "Executive Summary", "items": []}
    for raw_line in str(brief or "").splitlines():
        line = raw_line.strip()
        if not line or re.fullmatch(r"[-*_]{3,}", line):
            continue
        normalized = normalize_brief_line(line)
        heading = brief_section_heading(line)
        bullet_match = re.match(r"^(?:[-*]\s+|\d+\.\s+|[A-Z]\.\s+)(.*)$", normalized)
        if heading:
            if current["items"]:
                sections.append(current)
            current = {"title": heading, "items": []}
        elif bullet_match:
            current["items"].append({"kind": "bullet", "text": bullet_match.group(1)})
        else:
            current["items"].append({"kind": "text", "text": normalized})
    if current["items"]:
        sections.append(current)
    return sections


def brief_sections_to_html(brief: str) -> str:
    sections = parse_brief_sections(brief)
    if not sections:
        return "<div class='brief-section-grid'><div class='brief-section-card primary'><div class='brief-section-title'>Executive Summary</div><p>No brief content was generated.</p></div></div>"
    cards = []
    for idx, section in enumerate(sections):
        paragraphs = []
        bullets = []
        for item in section["items"]:
            if item["kind"] == "bullet":
                bullets.append(f"<li>{escape(str(item['text']))}</li>")
            else:
                paragraphs.append(f"<p>{escape(str(item['text']))}</p>")
        body = "".join(paragraphs)
        if bullets:
            body += "<ul class='brief-list'>" + "".join(bullets) + "</ul>"
        primary = " primary" if idx == 0 else ""
        cards.append(
            f"<div class='brief-section-card{primary}'>"
            f"<div class='brief-section-title'>{escape(str(section['title']))}</div>"
            f"{body}"
            "</div>"
        )
    return "<div class='brief-section-grid'>" + "".join(cards) + "</div>"


def render_executive_brief(run: Dict[str, Any], feature_df: pd.DataFrame, nvidia_key: str = "", nvidia_model: str = "") -> None:
    brief_source = str(run.get("brief_source", "unknown"))
    articles = run.get("articles", [])
    llm_audit = run.get("llm_audit", {})
    source_count = feature_df["source"].nunique() if not feature_df.empty and "source" in feature_df.columns else 0
    top_labels: List[str] = []
    top_level = ""
    avg_score_label = "Low"
    if not feature_df.empty:
        top_labels, top_level = top_signal_labels(feature_df)
        avg_score_label = risk_band(float(feature_df['risk_score'].mean()))
    articles_sent = llm_audit.get("articles_sent", min(len(articles), 8))
    attempts = llm_audit.get("nvidia_attempts", [])
    response_label = "not called"
    if attempts:
        response_label = f"{len(attempts)} attempt(s), {attempts[-1].get('elapsed_ms', 0)} ms last"
    header_note = (
        "Written by the Briefing Agent from the collected signal rows"
        if brief_source == "nvidia"
        else "Written by rule-based logic from the collected signal rows"
    )
    brief_source_label = "Agent" if brief_source == "nvidia" else "Rule-based"
    # Both the agent payload and the local fallback summarize only the three highest-priority rows.
    top_evidence_html = (
        "<ul class='headline-list'>" + "".join(f"<li>{escape(label)}</li>" for label in top_labels) + "</ul>"
        if top_labels else "<div class='brief-summary-text'>No signal</div>"
    )
    rows_used = llm_audit.get("feature_rows_sent", 0) if brief_source == "nvidia" else min(3, len(feature_df))
    brief_status_note = display_fallback_reason(
        llm_audit.get("fallback_reason") if brief_source != "nvidia" else ""
    ) if brief_source != "nvidia" else "Generated from the recorded top-signal payload; review the interpretation before use."
    html = (
        "<div class='brief-shell'>"
        "<div class='brief-header'>"
        "<div class='brief-kicker'>Executive Brief</div>"
        "<div class='brief-title'>External Signal Readout</div>"
        f"<div class='brief-summary-text'>{escape(header_note)}. {escape(str(brief_status_note))}</div>"
        "<div class='brief-meta-strip'>"
        f"<div class='brief-meta-chip'><div class='brief-meta-label'>Brief Source</div><div class='brief-meta-value'>{escape(brief_source_label)}</div></div>"
        f"<div class='brief-meta-chip'><div class='brief-meta-label'>Rows Grounded</div><div class='brief-meta-value'>{rows_used} rows used; {len(feature_df)} available</div></div>"
        f"<div class='brief-meta-chip'><div class='brief-meta-label'>Overall Signal Level</div><div class='brief-meta-value'>{escape(avg_score_label)}</div></div>"
        f"<div class='brief-meta-chip'><div class='brief-meta-label'>Agent timing</div><div class='brief-meta-value'>{escape(response_label)}</div></div>"
        "</div>"
        f"<div class='brief-summary-text' style='margin-top:10px;'>Top evidence{escape(' (' + top_level + ')') if top_level else ''}:</div>"
        f"{top_evidence_html}"
        f"<div class='brief-summary-text' style='margin-top:6px;'>Articles in context: {len(articles)} available, {articles_sent} sent.</div>"
        f"<div class='brief-summary-text' style='margin-top:6px;'>This brief summarizes the {rows_used} highest-priority signals of {len(feature_df)} and {articles_sent} supporting {'article' if articles_sent == 1 else 'articles'} chosen for relevance to them. Review Evidence and Export for every signal.</div>"
        "</div>"
        f"{brief_sections_to_html(str(run.get('brief', '')))}"
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)

    # If the initial NVIDIA call fell back because of a temporary timeout/service
    # issue, allow the planner to retry ONLY the brief. Collected API signals are
    # reused; the entire intelligence pipeline is not rerun.
    if brief_source != "nvidia":
        fallback_reason = display_fallback_reason(
            llm_audit.get("fallback_reason") or "The agent was unavailable."
        )
        if nvidia_key.strip():
            st.caption(f"This brief was written by rule-based logic. {fallback_reason}")
            if st.button(
                "Re-run Briefing Agent",
                key="retry_nvidia_executive_brief",
                type="primary",
            ):
                with st.spinner("Briefing Agent is rewriting the brief from the current collected signals..."):
                    run_config = run.get("run_config", {}) or {}
                    retry_brief, retry_source, retry_audit = generate_nvidia_brief(
                        nvidia_key.strip(),
                        nvidia_model.strip(),
                        feature_df,
                        run.get("articles", []),
                        str(run_config.get("retailer", "")),
                        str(run_config.get("region", "")),
                    )
                    run.setdefault("brief_revision_history", []).append({"replaced_at": utc_now(), "brief": run.get("brief"), "audit": run.get("llm_audit")})
                    run["brief"] = retry_brief
                    run["brief_source"] = retry_source
                    run["llm_audit"] = retry_audit
                    st.session_state["run"] = run
                    save_run_history(run, run_type="Brief regeneration", source="Saved source evidence")

                if retry_source == "nvidia":
                    st.success("Briefing Agent wrote the brief.")
                else:
                    st.warning(
                        "The agent is still unavailable, so the rule-based summary remains in use. "
                        + display_fallback_reason(
                            retry_audit.get("fallback_reason", "")
                        )
                    )
                st.rerun()
        else:
            st.caption(
                "This brief was written by rule-based logic because no LLM API key is "
                "configured for this session."
            )
