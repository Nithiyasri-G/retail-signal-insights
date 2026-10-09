from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from market_intelligence.history.event_repository import InMemoryEventHistoryRepository, ObservedEventHistory
from market_intelligence.history.uplift import (
    aggregate_observed_uplift,
    backtest_uplift,
    calculate_observed_uplift,
    demand_evidence_label,
    select_comparable_events,
)


UTC = timezone.utc
EVENT_START = datetime(2026, 6, 10, tzinfo=UTC)


def _event(event_id: str, *, fips: str = "48113", uplift: float = 20) -> ObservedEventHistory:
    return ObservedEventHistory(
        event_id=event_id,
        noaa_source_id=f"NWS-{event_id}",
        event_type="Flood Watch",
        hazard_family="flood",
        geography_fips=(fips,),
        severity="Moderate",
        season="summer",
        category="Emergency Essentials",
        baseline_start=EVENT_START - timedelta(days=28),
        baseline_end=EVENT_START - timedelta(days=1),
        event_start=EVENT_START,
        event_end=EVENT_START + timedelta(days=2),
        baseline_units=(100, 100, 100, 100, 100, 100, 100),
        event_units=(120, 120),
        observed_uplift_pct=uplift,
        traffic_change_pct=-10,
        data_coverage=0.95,
        sample_size=9,
        source_system="POS + NOAA",
    )


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"noaa_source_id": ""}, "noaa_source_id"),
        ({"baseline_units": (100,)}, "baseline_units"),
        ({"data_coverage": 0.5}, "data_coverage"),
        ({"sample_size": 2}, "sample_size"),
    ],
)
def test_observed_history_rejects_untraceable_or_incomplete_records(change, field) -> None:
    payload = _event("A").model_dump()
    payload.update(change)
    with pytest.raises(ValidationError) as error:
        ObservedEventHistory.model_validate(payload)
    assert any(item["loc"][0] == field for item in error.value.errors())


def test_observed_uplift_is_calculated_from_pos_windows() -> None:
    assert calculate_observed_uplift([100] * 7, [120, 130]) == 25.0
    with pytest.raises(ValueError, match="positive baseline"):
        calculate_observed_uplift([0] * 7, [10])


def test_comparable_selection_and_low_sample_gate() -> None:
    events = [_event("A"), _event("B", uplift=25), _event("C", uplift=30), _event("OTHER", fips="13121")]
    comparable = select_comparable_events(
        events,
        hazard_family="flood",
        geography_fips=["48113"],
        severity="Moderate",
        season="summer",
    )
    result = aggregate_observed_uplift(comparable)
    low_sample = aggregate_observed_uplift(comparable[:2])

    assert [event.event_id for event in comparable] == ["A", "B", "C"]
    assert result.quantitative and result.uplift_pct == 25.0
    assert result.evidence_label.startswith("Observed historical")
    assert not low_sample.quantitative and low_sample.uplift_pct is None
    assert demand_evidence_label("observed") == "Historical Observed Uplift"
    assert demand_evidence_label("synthetic") == "Scenario Demand Assumption"


def test_backtest_records_error_by_event_and_category() -> None:
    repository = InMemoryEventHistoryRepository([_event("A", uplift=20)])
    assert repository.list_events()[0].event_id == "A"
    rows = backtest_uplift(repository.list_events(), {"A": 15})

    assert rows == [
        {
            "event_id": "A",
            "event_type": "Flood Watch",
            "category": "Emergency Essentials",
            "predicted_uplift_pct": 15.0,
            "observed_uplift_pct": 20.0,
            "absolute_error_points": 5.0,
        }
    ]
    assert backtest_uplift(repository.list_events(), {}) == []


def test_observed_windows_must_be_ordered() -> None:
    payload = _event("A").model_dump()
    payload["baseline_end"] = payload["event_end"]
    with pytest.raises(ValidationError, match="ordered and non-overlapping"):
        ObservedEventHistory.model_validate(payload)
