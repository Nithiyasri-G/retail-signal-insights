from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DataProvenance:
    external_signal_mode: str
    internal_operations_mode: str
    explanation_mode: str
    as_of: str
    dataset_version: str

    @classmethod
    def live_signal_with_synthetic_operations(
        cls,
        *,
        as_of: str,
        explanation_mode: str = "Deterministic rules",
    ) -> "DataProvenance":
        return cls(
            external_signal_mode="Live",
            internal_operations_mode="Synthetic",
            explanation_mode=explanation_mode,
            as_of=as_of,
            dataset_version="fixtures-v1",
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)
