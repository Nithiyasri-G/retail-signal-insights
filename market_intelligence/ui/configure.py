from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunConfiguration:
    retailer: str
    region: str
    enabled_sources: tuple[str, ...]
    weather_areas: tuple[str, ...] = ()

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if not self.retailer.strip():
            errors.append("Retailer is required.")
        if not self.enabled_sources:
            errors.append("At least one source must be enabled.")
        return tuple(errors)
