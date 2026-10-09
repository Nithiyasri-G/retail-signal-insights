from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, model_validator

from .base import UtcTimestampedModel


class WeatherAlert(UtcTimestampedModel):
    alert_id: str = Field(min_length=1)
    event: str = Field(min_length=1)
    state: str = Field(pattern=r"^[A-Z]{2}$")
    area_description: str = Field(min_length=1)
    headline: str = ""
    starts_at: datetime
    ends_at: datetime
    source: str = Field(min_length=1)
    geometry: dict[str, Any] | None = None
    geocodes: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_window(self) -> "WeatherAlert":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        return self
