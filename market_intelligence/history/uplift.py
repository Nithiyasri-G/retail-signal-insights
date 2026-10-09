from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Iterable, Mapping

from .event_repository import ObservedEventHistory


@dataclass(frozen=True)
class ObservedUpliftResult:
    quantitative: bool
    uplift_pct: float | None
    traffic_change_pct: float | None
    comparable_event_ids: tuple[str, ...]
    evidence_label: str
    reason: str


def calculate_observed_uplift(
    baseline_units: Iterable[float],
    event_units: Iterable[float],
) -> float:
    baseline = [float(value) for value in baseline_units]
    event = [float(value) for value in event_units]
    if not baseline or not event or mean(baseline) <= 0:
        raise ValueError("positive baseline and non-empty event windows are required")
    return round(((mean(event) / mean(baseline)) - 1) * 100, 2)


def select_comparable_events(
    events: Iterable[ObservedEventHistory],
    *,
    hazard_family: str,
    geography_fips: Iterable[str],
    severity: str,
    season: str,
) -> list[ObservedEventHistory]:
    geography = set(geography_fips)
    return [
        event
        for event in events
        if event.hazard_family == hazard_family
        and event.severity == severity
        and event.season == season
        and bool(geography.intersection(event.geography_fips))
    ]


def aggregate_observed_uplift(
    events: Iterable[ObservedEventHistory],
    *,
    minimum_comparables: int = 3,
) -> ObservedUpliftResult:
    records = list(events)
    ids = tuple(record.event_id for record in records)
    if len(records) < minimum_comparables:
        return ObservedUpliftResult(
            quantitative=False,
            uplift_pct=None,
            traffic_change_pct=None,
            comparable_event_ids=ids,
            evidence_label="Observed history unavailable",
            reason=f"{len(records)} comparable observed event(s); {minimum_comparables} required.",
        )
    return ObservedUpliftResult(
        quantitative=True,
        uplift_pct=round(mean(record.observed_uplift_pct for record in records), 2),
        traffic_change_pct=round(mean(record.traffic_change_pct for record in records), 2),
        comparable_event_ids=ids,
        evidence_label="Observed historical POS and traffic evidence",
        reason=f"Aggregated {len(records)} comparable observed events.",
    )


def demand_evidence_label(provenance: str) -> str:
    return "Historical Observed Uplift" if provenance == "observed" else "Scenario Demand Assumption"


def backtest_uplift(
    events: Iterable[ObservedEventHistory],
    predictions_by_event: Mapping[str, float],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for event in events:
        if event.event_id not in predictions_by_event:
            continue
        predicted = float(predictions_by_event[event.event_id])
        observed = event.observed_uplift_pct
        rows.append(
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "category": event.category,
                "predicted_uplift_pct": predicted,
                "observed_uplift_pct": observed,
                "absolute_error_points": round(abs(predicted - observed), 2),
            }
        )
    return rows
