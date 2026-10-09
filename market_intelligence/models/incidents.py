from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import Field

from .base import StrictModel, UtcTimestampedModel


class DataMode(str, Enum):
    LIVE = "Live"
    SYNTHETIC = "Synthetic"
    OBSERVED = "Observed"
    CALCULATED = "Calculated"
    UNAVAILABLE = "Unavailable"


class ProvenanceRecord(UtcTimestampedModel):
    external_signal: DataMode
    internal_operations: DataMode
    explanation: DataMode
    dataset_version: str = Field(min_length=1)
    as_of: datetime


class AssumptionRecord(StrictModel):
    assumption_id: str
    version: str
    name: str
    value: float
    unit: str
    provenance: Literal[DataMode.SYNTHETIC] = DataMode.SYNTHETIC


class ObservedEventRecord(UtcTimestampedModel):
    event_id: str
    store_id: str
    sku: str
    event_type: str
    measured_value: float
    measured_at: datetime
    source_system: str


class AllocationRecord(StrictModel):
    store_id: str
    sku: str
    dc_id: str
    quantity: float = Field(ge=0)
    rank: int = Field(ge=1)


class Incident(UtcTimestampedModel):
    incident_id: str = Field(min_length=1)
    incident_type: Literal["Weather", "Product Recall"]
    source_record_id: str = Field(min_length=1)
    event: str = Field(min_length=1)
    status: str = Field(min_length=1)
    created_at: datetime
    provenance: ProvenanceRecord
    evidence: dict[str, Any] = Field(default_factory=dict)
