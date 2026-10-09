from html import escape
from market_intelligence.provenance.models import DataProvenance
from market_intelligence.provenance.presentation import provenance_labels
from market_intelligence.ui.operational_impact import operational_number_formats
from typing import Any
from typing import Dict
from typing import Optional
import pandas as pd
import streamlit as st


def confidence_class(confidence: str) -> str:
    lookup = {"High": "pill-high", "Medium": "pill-medium", "Low": "pill-low"}
    return lookup.get(confidence, "")


def render_audit_banner(provenance: DataProvenance) -> None:
    banner_class = "audit-banner warn" if provenance.internal_operations_mode != "Live" else "audit-banner"
    title = "Layer-specific data provenance"
    body = " | ".join(provenance_labels(provenance))
    st.markdown(
        f"<div class='{banner_class}'><div class='audit-title'>{escape(title)}</div><div class='audit-body'>{escape(body)}</div></div>",
        unsafe_allow_html=True,
    )


def render_audit_card(label: str, value: str, note: str) -> None:
    st.markdown(
        "<div class='audit-card'>"
        f"<div class='audit-label'>{escape(label)}</div>"
        f"<div class='audit-value'>{escape(value)}</div>"
        f"<div class='audit-note'>{escape(note)}</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def render_metric_card(title: str, value: str, note: str, confidence: str = "", value_class: str = "") -> None:
    level_key = str(value or "").strip().lower() if str(value or "").strip().lower() in {"high", "medium", "low"} else ""
    # An explicit value_class (e.g. a big colored number sitting next to other big
    # numbers) always wins over the automatic High/Medium/Low badge styling -- stacking
    # both previously shrank the value to badge-sized text even when the caller asked
    # for the large, number-style treatment, which is what made some cards look out of
    # place next to their row neighbors.
    level_class = f" level-{level_key}" if level_key and not value_class else ""
    pill = f'<span class="pill {confidence_class(confidence)}">{confidence}</span>' if confidence and not level_key else ""
    note_html = f'<div class="metric-note">{note}</div>' if note else ""
    html = (
        f'<div class="metric-card{level_class}">'
        f'<div class="metric-label">{title}</div>'
        f'<div class="metric-value{level_class}{" " + value_class if value_class else ""}">{value}</div>'
        f"{pill}"
        f"{note_html}"
        "</div>"
    )
    st.markdown(
        html,
        unsafe_allow_html=True,
    )


def style_level_dataframe(df: pd.DataFrame):
    def level_style(value: Any) -> str:
        level = str(value or "").strip().lower()
        if level == "high":
            return "background-color:#FEE2E2;color:#B91C1C;font-weight:800"
        if level == "medium":
            return "background-color:#FEF3C7;color:#B45309;font-weight:800"
        if level == "low":
            return "background-color:#DCFCE7;color:#15803D;font-weight:800"
        return ""
    styler = df.style
    style_map = styler.map if hasattr(styler, "map") else styler.applymap
    return style_map(level_style)


def style_operational_dataframe(df: pd.DataFrame, row_level_column: Optional[str] = None, emphasise_cells: bool = True):
    """Color-code Operational Impact Center tables (Priority/Urgency/Staffing Risk/Route
    Risk/Decision Status/Inventory Gap) using the same red/amber/green language as
    style_level_dataframe, applied per-column since these tables mix risk-bearing columns
    with plain identifiers rather than being uniformly High/Medium/Low."""
    red, amber, green = "background-color:#FEE2E2;color:#B91C1C;font-weight:800", "background-color:#FEF3C7;color:#B45309;font-weight:800", "background-color:#DCFCE7;color:#15803D;font-weight:800"
    neutral = "background-color:#E2E8F0;color:#475569;font-weight:700"

    def level_cell(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text.startswith("high"):
            return red
        if text.startswith("elevated") or text == "medium":
            return amber
        if text.startswith("low"):
            return green
        return ""

    def yesno_cell(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text == "yes":
            return red
        if text == "no":
            return green
        return ""

    def decision_cell(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text == "approved":
            return green
        if text == "rejected":
            return red
        if text == "modified":
            return amber
        if text == "proposed":
            return neutral
        return ""

    def gap_cell(value: Any) -> str:
        try:
            return red if float(value) > 0 else green
        except (TypeError, ValueError):
            return ""

    styler = df.style
    style_map = styler.map if hasattr(styler, "map") else styler.applymap

    # Optional whole-row tint from a High / Medium / Low column, applied first so the cell-level
    # colours below stay on top of it. Rows without a level are left plain.
    if row_level_column and row_level_column in df.columns:
        row_tints = {
            "high": "background-color:#FEE2E2",
            "medium": "background-color:#FEF3C7",
            "elevated": "background-color:#FEF3C7",
            "low": "background-color:#DCFCE7",
            # Replenishment status: red only where a shortfall is still uncovered after DC allocation.
            "escalate": "background-color:#FEE2E2",
            "covered": "background-color:#FEF3C7",
        }

        def row_tint(row: pd.Series) -> list:
            tint = row_tints.get(str(row.get(row_level_column, "") or "").strip().lower(), "")
            return [tint] * len(row)

        styler = styler.apply(row_tint, axis=1)

    def action_cell(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text == "act":
            return red
        if text == "review":
            return amber
        if text == "monitor":
            return green
        return ""

    def evidence_strength_cell(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text == "strong":
            return green
        if text == "moderate":
            return amber
        if text == "limited":
            return neutral
        return ""

    for col in ("Priority", "Operational Priority", "Urgency", "Staffing Risk", "Level"):
        if col in df.columns:
            styler = style_map(level_cell, subset=[col])
    if "Actionability" in df.columns:
        styler = style_map(action_cell, subset=["Actionability"])
    if "Evidence Strength" in df.columns:
        styler = style_map(evidence_strength_cell, subset=["Evidence Strength"])
    if emphasise_cells and "Route Risk" in df.columns:
        styler = style_map(yesno_cell, subset=["Route Risk"])
    for col in ("Decision Status", "Decision"):
        if col in df.columns:
            styler = style_map(decision_cell, subset=[col])
    # "Gap" / "Remaining Gap" are the display names the DC Replenishment Plan table
    # renames "Inventory Gap" / "Residual Gap" to; without these aliases the gap
    # highlighting was silently lost the moment those columns were renamed for display.
    for col in ("Inventory Gap", "Gap", "Residual Gap", "Remaining Gap"):
        if emphasise_cells and col in df.columns:
            styler = style_map(gap_cell, subset=[col])
    number_formats = operational_number_formats(list(df.columns))
    if number_formats:
        styler = styler.format(number_formats, na_rep="")
    return styler


def render_action_card(label: str, title: str, body: str) -> None:
    html = (
        '<div class="action-card">'
        f'<div class="action-label">{escape(label)}</div>'
        f'<div class="action-title">{escape(title)}</div>'
        f'<div class="action-body">{escape(body)}</div>'
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def render_source_tile(name: str, status: str, detail: str, purpose: str = "") -> None:
    status_class = "pill-high" if status == "Active" else "pill-medium" if status == "Optional" else "pill-low"
    tile_state = "active" if status == "Active" else "off"
    purpose_html = f"<div class='source-purpose'>{escape(purpose)}</div>" if purpose else ""
    html = (
        f'<div class="source-tile {tile_state}">'
        f'<div class="source-name">{escape(name)} <span class="pill {status_class}" style="margin-left:6px;margin-top:0;">{escape(status)}</span></div>'
        f'<div class="source-meta">{escape(detail)}</div>'
        f"{purpose_html}"
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def render_run_monitor(slot: Any, title: str, states: Dict[str, Dict[str, str]], progress_pct: int) -> None:
    cards = []
    for name, info in states.items():
        status = info.get("status", "queued")
        detail = info.get("detail", "")
        status_class = {
            "running": "status-running",
            "success": "status-success",
            "failed": "status-failed",
            "skipped": "status-skipped",
            "queued": "status-queued",
        }.get(status, "status-queued")
        cards.append(
            "<div class='run-status-card'>"
            f"<div class='status-badge {status_class}'>{escape(status)}</div>"
            f"<div class='run-status-name'>{escape(name)}</div>"
            f"<div class='run-status-detail'>{escape(detail)}</div>"
            "</div>"
        )
    html = (
        "<div class='run-monitor'>"
        "<div class='run-monitor-head'>"
        f"<div><div class='run-monitor-sub'>Pipeline Status</div><div class='run-monitor-title'>{escape(title)}</div></div>"
        f"<div class='tbadge'>{int(progress_pct)}%</div>"
        "</div>"
        "<div class='run-progress-track'>"
        f"<div class='run-progress-fill' style='width:{max(0, min(100, int(progress_pct)))}%;'></div>"
        "</div>"
        "<div class='run-status-grid'>"
        + "".join(cards)
        + "</div></div>"
    )
    slot.markdown(html, unsafe_allow_html=True)


def render_sidebar_status(slot: Any, message: str, state: str = "info") -> None:
    if state == "success":
        slot.success(message)
    elif state == "warning":
        slot.warning(message)
    elif state == "error":
        slot.error(message)
    else:
        slot.info(message)
