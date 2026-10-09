from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import StrictModel, UtcTimestampedModel


class Store(StrictModel):
    store_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    state: str = Field(pattern=r"^[A-Z]{2}$")
    county_fips: str = Field(pattern=r"^\d{5}$")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    operating_status: Literal["Open", "Closed"] = "Open"


class StoreSkuSourcingLane(StrictModel):
    store_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    dc_id: str = Field(min_length=1)
    rank: int = Field(ge=1)
    transit_hours: float = Field(ge=0)
    service_level: Literal["primary", "backup"]
    active: bool = True


class RouteLane(StrictModel):
    dc_id: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    eta_hours: float = Field(ge=0)
    eligible: bool
    route_counties: tuple[str, ...] = ()


class StaffingShift(UtcTimestampedModel):
    store_id: str = Field(min_length=1)
    employee_token: str = Field(min_length=1)
    shift_start: datetime
    shift_end: datetime
    scheduled_hours: float = Field(gt=0, le=24)
    on_call_eligible: bool = False
