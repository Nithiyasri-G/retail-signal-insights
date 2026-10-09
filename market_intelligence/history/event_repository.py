from __future__ import annotations

from datetime import datetime
from typing import Iterable, Protocol

from pydantic import Field, model_validator

from market_intelligence.models.base import UtcTimestampedModel


class ObservedEventHistory(UtcTimestampedModel):
    event_id: str = Field(min_length=1)
    noaa_source_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    hazard_family: str = Field(min_length=1)
    geography_fips: tuple[str, ...] = Field(min_length=1)
    severity: str = Field(min_length=1)
    season: str = Field(pattern=r"^(winter|spring|summer|fall)$")
    category: str = Field(min_length=1)
    baseline_start: datetime
    baseline_end: datetime
    event_start: datetime
    event_end: datetime
    baseline_units: tuple[float, ...] = Field(min_length=7)
    event_units: tuple[float, ...] = Field(min_length=1)
    observed_uplift_pct: float
    traffic_change_pct: float
    data_coverage: float = Field(ge=0.8, le=1)
    sample_size: int = Field(ge=7)
    source_system: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_windows_and_sample(self) -> "ObservedEventHistory":
        if not (self.baseline_start < self.baseline_end <= self.event_start < self.event_end):
            raise ValueError("baseline and event windows must be ordered and non-overlapping")
        if self.sample_size != len(self.baseline_units) + len(self.event_units):
            raise ValueError("sample_size must equal baseline plus event observations")
        return self


class EventHistoryRepository(Protocol):
    def list_events(self) -> list[ObservedEventHistory]: ...


class InMemoryEventHistoryRepository:
    def __init__(self, events: Iterable[ObservedEventHistory]) -> None:
        self._events = list(events)

    def list_events(self) -> list[ObservedEventHistory]:
        return list(self._events)
