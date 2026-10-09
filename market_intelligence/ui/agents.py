"""The agents shown on screen, named by role, and the credit line that sits under their output."""

from typing import List, Tuple

import streamlit as st

BRIEFING_AGENT = "Briefing Agent"
PLANNER_AGENT = "Replenishment Planner Agent"
ANALYST_AGENT = "Incident Analyst Agent"
DEMAND_AGENT = "Demand Analyst Agent"


def agent_credit(agent: str, source: str) -> str:
    """"Prepared by Demand Analyst Agent", plus "(rule-based)" when the local summary was used.

    ``source`` is the token the report generators return: "AI summary" when the language model's
    answer was accepted, "Local summary" when the rule-based text was used, "No gap" when there
    was nothing to recommend.
    """
    if source == "No gap":
        return "No shortfall to cover."
    suffix = " (rule-based)" if source == "Local summary" else ""
    return f"Prepared by {agent}{suffix}"


def agent_text_sections(explanation: str) -> List[Tuple[str, str]]:
    """Split the agent's text on its ALL-CAPS heading lines (INCIDENT SUMMARY, LIMITATIONS, ...)."""
    sections: List[Tuple[str, str]] = []
    heading = ""
    body: List[str] = []
    for line in explanation.splitlines():
        stripped = line.strip()
        is_heading = (
            bool(stripped)
            and stripped == stripped.upper()
            and len(stripped) <= 40
            and any(ch.isalpha() for ch in stripped)
            and not stripped.startswith(("-", "*", "#"))
        )
        if is_heading:
            if heading or any(part.strip() for part in body):
                sections.append((heading, "\n".join(body).strip()))
            heading, body = stripped.title(), []
        else:
            body.append(line)
    sections.append((heading, "\n".join(body).strip()))
    return [(h, b) for h, b in sections if h or b]


def render_agent_text(explanation: str) -> None:
    """Readable headings; the long list of calculated figures is already on the Impact tab, so it is folded away."""
    for heading, body in agent_text_sections(explanation):
        if heading and "calculated" in heading.lower():
            with st.expander(heading, expanded=False):
                st.markdown(body)
            continue
        if heading:
            st.markdown(f"**{heading}**")
        if body:
            st.markdown(body)
