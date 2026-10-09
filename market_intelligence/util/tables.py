from typing import Any
from typing import List
import pandas as pd


def sum_numeric_column(df: Any, column: str) -> float:
    """Total one column, tolerating a frame that does not have it.

    `DataFrame.get(column, 0)` returns the scalar 0 when the column is absent, and a
    scalar has no .fillna(), so the caller crashed with
    "'int' object has no attribute 'fillna'". Persisted incidents from earlier runs do
    not all carry every column, so this has to be safe rather than assumed.
    """
    if not isinstance(df, pd.DataFrame) or df.empty or column not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[column], errors="coerce").fillna(0).sum())


def format_timestamp(value: Any) -> str:
    """Render a stored UTC timestamp for a reader, not for a log parser."""
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = pd.to_datetime(text, errors="coerce", utc=True)
    return parsed.strftime("%d %b %Y, %H:%M UTC") if not pd.isna(parsed) else text


def format_recall_date(value: Any) -> str:
    """openFDA returns recall dates as YYYYMMDD strings."""
    text = str(value or "").strip()
    if not text:
        return "Not supplied"
    parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    if pd.isna(parsed):
        parsed = pd.to_datetime(text, errors="coerce")
    return parsed.strftime("%d %b %Y") if not pd.isna(parsed) else text


def format_as_of_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Render inventory as-of stamps as a date, not a raw ISO timestamp.

    These cells were showing "2026-09-23T00:00:00+00:00" -- the machine form of a value
    whose only job is to tell a planner how fresh the count is.
    """
    if df is None or df.empty:
        return df
    formatted = df.copy()
    for column in formatted.columns:
        if not str(column).endswith("As Of"):
            continue
        parsed = pd.to_datetime(formatted[column], errors="coerce", utc=True)
        formatted[column] = parsed.dt.strftime("%d %b %Y").fillna(
            formatted[column].astype(str).replace({"NaT": "", "nan": "", "None": ""})
        )
    return formatted


def drop_empty_columns(df: pd.DataFrame, columns: List[str]) -> List[str]:
    """Keep only the columns that actually carry a value in this run.

    A column of blanks in a client-facing table reads as a defect, and the reader has
    no way to tell "nothing to report" from "this is broken". Rather than curating the
    column list by hand per run, any column that is empty for every row on screen is
    simply not shown. A column left with at least one blank cell is kept, because there
    the blank is information about that row.
    """
    kept: List[str] = []
    for column in columns:
        if column not in df.columns:
            continue
        values = df[column].astype(str).str.strip()
        if (values.isin({"", "nan", "none", "None", "<NA>"})).all():
            continue
        kept.append(column)
    return kept
