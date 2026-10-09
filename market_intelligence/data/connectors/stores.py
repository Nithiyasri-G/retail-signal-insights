from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from market_intelligence.models.stores import Store


class StoreConnector:
    def __init__(self, fetch: Callable[[datetime], Iterable[Mapping[str, Any]]]) -> None:
        self.fetch = fetch

    def get_stores(self, as_of: datetime) -> list[Store]:
        stores = [Store.model_validate(row) for row in self.fetch(as_of)]
        ids = [store.store_id for store in stores]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate Store IDs returned by connector")
        if not stores:
            raise ValueError("Store connector returned no records")
        return stores
