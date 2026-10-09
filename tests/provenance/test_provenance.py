from market_intelligence.provenance.models import DataProvenance
from market_intelligence.provenance.presentation import provenance_labels, provenance_summary


def test_live_external_and_synthetic_internal_are_labeled_separately() -> None:
    provenance = DataProvenance(
        external_signal_mode="Live",
        internal_operations_mode="Synthetic",
        explanation_mode="Deterministic",
        as_of="2026-09-23T12:00:00+00:00",
        dataset_version="fixtures-v1",
    )

    labels = provenance_labels(provenance)

    assert labels[0] == "External signal: Live"
    assert labels[1] == "Internal operational data: Synthetic"
    assert "fixtures-v1" in labels[3]


def test_layered_provenance_cannot_claim_no_mock_data_for_synthetic_internal_data() -> None:
    provenance = DataProvenance(
        external_signal_mode="Live",
        internal_operations_mode="Synthetic",
        explanation_mode="NVIDIA",
        as_of="2026-09-23T12:00:00+00:00",
        dataset_version="fixtures-v1",
    )

    summary = provenance_summary(provenance)

    assert "No Mock Data Used In This Run" not in summary
    assert "Synthetic" in summary


def test_provenance_serializes_into_export_payload() -> None:
    """The default wording is client-facing: "Synthetic fixtures-v1" read as a defect."""
    provenance = DataProvenance.live_signal_with_synthetic_operations(
        as_of="2026-09-23T12:00:00+00:00",
        explanation_mode="Rules + AI summary",
    )

    payload = provenance.to_dict()

    assert payload["external_signal_mode"] == "Live"
    assert payload["internal_operations_mode"] == "Synthetic"
    assert payload["dataset_version"] == "fixtures-v1"
    # Whatever the wording, the internal layer must never be labelled as live.
    assert payload["internal_operations_mode"] != "Live"
