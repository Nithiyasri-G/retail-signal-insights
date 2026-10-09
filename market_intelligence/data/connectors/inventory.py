from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from market_intelligence.models.inventory import InventorySnapshot


class InventoryConnector:
    def __init__(
        self,
        fetch: Callable[[datetime], Iterable[Mapping[str, Any]]],
        *,
        max_age_hours: float = 24,
    ) -> None:
        self.fetch = fetch
        self.max_age_hours = max_age_hours

    def get_inventory(self, as_of: datetime) -> list[InventorySnapshot]:
        snapshots = [InventorySnapshot.model_validate(row) for row in self.fetch(as_of)]
        keys = [(row.location_id, row.sku) for row in snapshots]
        if not snapshots or len(keys) != len(set(keys)):
            raise ValueError("Inventory connector returned no snapshots or duplicate location/SKU rows")
        stale = [row for row in snapshots if not row.is_fresh(at=as_of, max_age_hours=self.max_age_hours)]
        if stale:
            raise ValueError(f"Inventory connector returned {len(stale)} stale snapshot(s)")
        return snapshots
