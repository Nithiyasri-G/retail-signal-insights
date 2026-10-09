from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class RecallSignal:
    recall_number: str
    product_description: str
    classification: str
    status: str
    code_info: str


def normalize_openfda_recall(record: Mapping[str, Any]) -> RecallSignal:
    return RecallSignal(
        recall_number=str(record.get("recall_number") or ""),
        product_description=str(record.get("product_description") or ""),
        classification=str(record.get("classification") or ""),
        status=str(record.get("status") or ""),
        code_info=str(record.get("code_info") or ""),
    )
