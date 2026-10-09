from market_intelligence.provenance.models import DataProvenance
from market_intelligence.provenance.presentation import provenance_labels


def evidence_layer_cards(provenance: DataProvenance) -> tuple[str, ...]:
    return provenance_labels(provenance)
