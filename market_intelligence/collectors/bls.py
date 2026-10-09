from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class BlsObservation:
    series_id: str
    year: int
    period: str
    value: float


def normalize_bls_observation(series_id: str, record: Mapping[str, Any]) -> BlsObservation:
    return BlsObservation(series_id, int(record["year"]), str(record["period"]), float(record["value"]))
