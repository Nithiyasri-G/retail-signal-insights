from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WeatherAssumptionSet:
    version: str
    event_type: str
    region: str
    phase_uplift: dict[str, float] = field(default_factory=dict)
    traffic_delta: dict[str, float] = field(default_factory=dict)
    provenance: str = "synthetic"

    def display_labels(self) -> dict[str, str]:
        if self.provenance == "observed":
            return {
                "demand": "Observed Historical Demand Uplift",
                "traffic": "Observed Historical Traffic Delta",
                "comparables": "Observed Comparable Events",
            }
        return {
            "demand": "Scenario Demand Assumption",
            "traffic": "Scenario Traffic Assumption",
            "comparables": "Comparable Scenario Cases",
        }

    @property
    def evidence_label(self) -> str:
        if self.provenance == "observed":
            return f"Observed event-history dataset ({self.version})"
        return f"Versioned POC assumption set ({self.version})"
