from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from market_intelligence.models.stores import StoreSkuSourcingLane


class SourcingConnector:
    def __init__(self, fetch: Callable[[datetime], Iterable[Mapping[str, Any]]]) -> None:
        self.fetch = fetch

    def get_store_sku_lanes(self, as_of: datetime) -> list[StoreSkuSourcingLane]:
        lanes = [StoreSkuSourcingLane.model_validate(row) for row in self.fetch(as_of)]
        keys = [(lane.store_id, lane.sku, lane.rank) for lane in lanes]
        if not lanes or len(keys) != len(set(keys)):
            raise ValueError("Sourcing connector returned no lanes or duplicate Store/SKU ranks")
        return lanes
