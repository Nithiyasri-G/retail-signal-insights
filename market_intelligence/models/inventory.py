from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import Field, model_validator

from .base import UtcTimestampedModel


class InventorySnapshot(UtcTimestampedModel):
    location_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    on_hand: float = Field(ge=0)
    committed: float = Field(ge=0)
    safety_stock: float = Field(ge=0)
    eligible_inbound_before_cutoff: float = Field(ge=0)
    atp: float = Field(ge=0)
    captured_at: datetime

    @model_validator(mode="after")
    def validate_atp(self) -> "InventorySnapshot":
        calculated = max(
            0.0,
            self.on_hand
            - self.committed
            - self.safety_stock
            + self.eligible_inbound_before_cutoff,
        )
        if abs(self.atp - calculated) > 0.001:
            raise ValueError(
                "atp must equal max(0, on_hand - committed - safety_stock + eligible_inbound_before_cutoff)"
            )
        return self

    def is_fresh(self, *, at: datetime, max_age_hours: float) -> bool:
        if at.tzinfo is None or at.utcoffset() != timedelta(0):
            raise ValueError("freshness evaluation time must be timezone-aware UTC")
        age = at - self.captured_at
        return timedelta(0) <= age <= timedelta(hours=max_age_hours)
