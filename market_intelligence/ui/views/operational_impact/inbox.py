"""Shared inbox pieces for the Weather and Product recall tabs."""
from html import escape
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import streamlit as st

from market_intelligence.ui.components import style_operational_dataframe

ROW_HEIGHT_PX = 35
MAX_TABLE_HEIGHT_PX = 310
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def live_feed_note(run: Optional[Dict[str, Any]], key: str, label: str) -> str:
    """Why a tab has no live incidents: not selected, failed (with the reason), or genuinely empty."""
    result = ((run or {}).get("results") or {}).get(key)
    if result is None:
        return f"{label} was not collected in the latest run (the source was not selected)."
    if result.get("status") != "success":
        reason = str(result.get("error") or "no reason was reported").strip().rstrip(".")
        return f"{label} could not be collected in the latest run: {reason}."
    if not result.get("items"):
        return f"{label} returned no active items in the latest run."
    return ""


def _table_height(row_count: int) -> int:
    """Tall enough to show every row (header included) up to a cap, then scroll."""
    return min(ROW_HEIGHT_PX * (row_count + 1) + 3, MAX_TABLE_HEIGHT_PX)


def incident_filters(
    df: pd.DataFrame,
    key: str,
    *,
    priority_col: str,
    type_col: str,
    type_all: str,
    label_cols: Sequence[str] = (),
    extra: Sequence[Tuple[str, str]] = (),
    with_export_slot: bool = False,
):
    """One compact row of filters above an incident table.

    The first control is a searchable dropdown of the incidents themselves: type to narrow the
    list, pick one and it is pinned to the top of the table (call ``pin_selected`` after any
    re-sorting). Priority, the type column and any ``extra`` (column, "All ...") pairs are
    single-choice. Returns the filtered frame, ordered High to Low priority (stable, so the
    existing order holds within a level), and the last column of the row when
    ``with_export_slot`` is set, so the caller can put an export button there.
    """
    selects: List[Tuple[str, str, str]] = [(priority_col, "All priorities", "priority"), (type_col, type_all, "type")]
    selects += [(col, label, f"extra_{i}") for i, (col, label) in enumerate(extra)]
    selects = [s for s in selects if s[0] in df.columns]
    widths = [2.4] + [1.0] * len(selects) + ([0.9] if with_export_slot else [])
    columns = st.columns(widths, vertical_alignment="bottom")

    result = df
    for position, (col, all_label, tag) in enumerate(selects, start=1):
        values = [v for v in df[col].dropna().astype(str).unique().tolist() if v.strip()]
        if tag == "priority":
            values = sorted(values, key=lambda v: PRIORITY_ORDER.get(v.strip().lower(), 9))
        else:
            values = sorted(values)
        with columns[position]:
            choice = st.selectbox(col, [all_label] + values, key=f"{key}_{tag}", label_visibility="collapsed")
        if choice != all_label:
            result = result[result[col].astype(str) == choice]
    if priority_col in result.columns:
        order = result[priority_col].astype(str).str.strip().str.lower().map(PRIORITY_ORDER).fillna(9)
        result = result.assign(_priority_rank=order).sort_values("_priority_rank", kind="stable").drop(columns="_priority_rank")

    # The incident dropdown lists whatever the other filters leave, so it never offers a row
    # the table would not show.
    pinned_id = None
    parts = [c for c in label_cols if c in result.columns]
    with columns[0]:
        if parts and "Incident ID" in result.columns and not result.empty:
            labels: Dict[str, str] = {}
            for _, row in result.iterrows():
                text = " · ".join(str(row[c]).strip() for c in parts if str(row[c]).strip())
                label = text
                if label in labels:
                    label = f"{text} ({len(labels)})"
                labels[label] = str(row["Incident ID"])
            picked = st.selectbox(
                "Find an incident",
                list(labels),
                index=None,
                placeholder="Find an incident",
                key=f"{key}_find",
                label_visibility="collapsed",
            )
            pinned_id = labels.get(picked) if picked else None
        else:
            st.selectbox("Find an incident", [], index=None, placeholder="Find an incident", key=f"{key}_find_empty", label_visibility="collapsed")
    st.session_state[f"{key}_pinned_id"] = pinned_id
    result = pin_selected(result, key)
    return result, (columns[-1] if with_export_slot else None)


def pin_selected(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """Move the incident chosen in the dropdown to the first row (a no-op when none is chosen)."""
    pinned_id = st.session_state.get(f"{key}_pinned_id")
    if not pinned_id or "Incident ID" not in df.columns:
        return df
    is_pinned = df["Incident ID"].astype(str) == str(pinned_id)
    if not is_pinned.any():
        return df
    return pd.concat([df[is_pinned], df[~is_pinned]])

def incident_table(
    display_df: pd.DataFrame,
    key: str,
    column_config: Optional[Dict[str, Any]] = None,
    priority_col: Optional[str] = None,
) -> Optional[int]:
    """Render an incident table where a click on a row selects it.

    The whole row is tinted red, amber or green from ``priority_col`` so urgent incidents stand
    out. Returns the position of the clicked row within ``display_df`` or ``None`` when no row has
    been selected yet. The caller keeps its own list of incident IDs in the same order, so the
    position is all it needs.
    """
    event = st.dataframe(
        style_operational_dataframe(display_df, row_level_column=priority_col),
        width="stretch",
        height=_table_height(len(display_df)),
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
        column_config=column_config,
    )
    try:
        rows = list(event.selection.rows)
    except AttributeError:
        rows = []
    return rows[0] if rows else None


def stat_row(items: Dict[str, Any]) -> None:
    """A single row of key numbers, used at the top of an incident's detail."""
    cells = "".join(
        f"<div class='stat-cell'><div class='stat-label'>{escape(str(label))}</div>"
        f"<div class='stat-value'>{escape(str(value))}</div></div>"
        for label, value in items.items()
    )
    st.markdown(f"<div class='stat-row'>{cells}</div>", unsafe_allow_html=True)

def kpi_row(items: List[Tuple[str, str, str]]) -> None:
    """Large headline numbers for an incident. Each item is (label, value, tone) where tone is
    "bad", "good" or "" and only colours the number."""
    cells = "".join(
        f"<div class='kpi-cell'><div class='kpi-label'>{escape(label)}</div>"
        f"<div class='kpi-value {escape(tone)}'>{escape(value)}</div></div>"
        for label, value, tone in items
    )
    st.markdown(f"<div class='kpi-row'>{cells}</div>", unsafe_allow_html=True)

def kv_list(rows: List[Tuple[str, str]]) -> None:
    """Label / value rows in a bordered list. Values are HTML the caller has already escaped."""
    body = "".join(
        f"<div class='kv-row'><div class='kv-label'>{escape(label)}</div><div class='kv-value'>{value}</div></div>"
        for label, value in rows
    )
    st.markdown(f"<div class='kv-list'>{body}</div>", unsafe_allow_html=True)
