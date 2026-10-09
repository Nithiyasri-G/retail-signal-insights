from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class UtcTimestampedModel(StrictModel):
    @field_validator(
        "created_at", "as_of", "captured_at", "starts_at", "ends_at",
        "measured_at", "shift_start", "shift_end", "baseline_start", "baseline_end",
        "event_start", "event_end", check_fields=False,
    )
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("timestamp must be timezone-aware UTC")
        return value
