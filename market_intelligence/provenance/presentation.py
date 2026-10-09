from __future__ import annotations

from .models import DataProvenance


def provenance_labels(provenance: DataProvenance) -> tuple[str, str, str, str]:
    return (
        f"External signal: {provenance.external_signal_mode}",
        f"Internal operational data: {provenance.internal_operations_mode}",
        f"Explanation layer: {provenance.explanation_mode}",
        f"Internal dataset: {provenance.dataset_version}; as of {provenance.as_of}",
    )


def provenance_summary(provenance: DataProvenance) -> str:
    return " | ".join(provenance_labels(provenance))
