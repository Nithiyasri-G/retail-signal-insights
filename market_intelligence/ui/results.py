from __future__ import annotations

from typing import Any, Iterable, Mapping


def result_summary_rows(features: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "Source": row.get("source", ""),
            "Signal": row.get("signal_name", ""),
            "Risk": row.get("risk_score", 0),
            "Evidence": row.get("reason", ""),
        }
        for row in features
    ]
