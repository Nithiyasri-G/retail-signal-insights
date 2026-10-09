from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from market_intelligence.models.stores import StaffingShift


class StaffingConnector:
    def __init__(self, fetch: Callable[[datetime, datetime], Iterable[Mapping[str, Any]]]) -> None:
        self.fetch = fetch

    def get_staffing_window(self, start: datetime, end: datetime) -> list[StaffingShift]:
        shifts = [StaffingShift.model_validate(row) for row in self.fetch(start, end)]
        if not shifts:
            raise ValueError("Staffing connector returned no shifts")
        if any(shift.shift_start < start or shift.shift_end > end for shift in shifts):
            raise ValueError("Staffing connector returned shifts outside requested window")
        return shifts
