from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from market_intelligence.models.stores import RouteLane


class RouteConnector:
    def __init__(self, fetch: Callable[[datetime], Iterable[Mapping[str, Any]]]) -> None:
        self.fetch = fetch

    def get_routes(self, as_of: datetime) -> list[RouteLane]:
        routes = [RouteLane.model_validate(row) for row in self.fetch(as_of)]
        keys = [(route.dc_id, route.store_id) for route in routes]
        if not routes or len(keys) != len(set(keys)):
            raise ValueError("Route connector returned no routes or duplicate DC/Store rows")
        return routes
