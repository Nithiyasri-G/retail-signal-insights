from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class NewsSignal:
    title: str
    url: str
    published_at: str
    publisher: str


def normalize_news_item(record: Mapping[str, Any]) -> NewsSignal:
    return NewsSignal(
        title=str(record.get("title") or ""),
        url=str(record.get("url") or record.get("link") or ""),
        published_at=str(record.get("published_at") or record.get("published") or ""),
        publisher=str(record.get("publisher") or record.get("source") or ""),
    )
