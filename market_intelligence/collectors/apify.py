from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class SearchInterestSignal:
    keyword: str
    geography: str
    interest: float
    captured_at: str


def normalize_apify_interest(record: Mapping[str, Any], *, geography: str) -> SearchInterestSignal:
    return SearchInterestSignal(
        keyword=str(record.get("keyword") or record.get("query") or ""),
        geography=geography,
        interest=float(record.get("interest") or record.get("value") or 0),
        captured_at=str(record.get("captured_at") or record.get("date") or ""),
    )
