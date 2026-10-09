from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Any


@dataclass(frozen=True)
class RecallMatch:
    status: str
    sku: str
    reason: str


def match_recall_upc(upcs: Iterable[str], products: Iterable[Mapping[str, Any]]) -> RecallMatch:
    normalized = {"".join(ch for ch in value if ch.isdigit()) for value in upcs}
    for product in products:
        sku = "".join(ch for ch in str(product.get("sku") or product.get("UPC") or "") if ch.isdigit())
        if sku and sku in normalized:
            return RecallMatch("confirmed_exact", sku, "Exact normalized UPC match.")
    return RecallMatch("unmatched", "", "No exact UPC match in the configured product catalog.")
