from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Protocol, TypeVar

from market_intelligence.data.connectors.inventory import InventoryConnector
from market_intelligence.data.connectors.routes import RouteConnector
from market_intelligence.data.connectors.sourcing import SourcingConnector
from market_intelligence.data.connectors.staffing import StaffingConnector
from market_intelligence.data.connectors.stores import StoreConnector
from market_intelligence.data.fixture_repository import FixtureRepository
from market_intelligence.models.inventory import InventorySnapshot
from market_intelligence.models.stores import RouteLane, StaffingShift, Store, StoreSkuSourcingLane


class InternalDataUnavailable(RuntimeError):
    pass


T = TypeVar("T")


class OperationalDataRepository(Protocol):
    def get_stores(self, as_of: datetime) -> list[Store]: ...
    def get_store_sku_lanes(self, as_of: datetime) -> list[StoreSkuSourcingLane]: ...
    def get_inventory(self, as_of: datetime) -> list[InventorySnapshot]: ...
    def get_routes(self, as_of: datetime) -> list[RouteLane]: ...
    def get_staffing_window(self, start: datetime, end: datetime) -> list[StaffingShift]: ...


class ConnectedOperationalRepository:
    def __init__(
        self,
        *,
        stores: StoreConnector,
        sourcing: SourcingConnector,
        inventory: InventoryConnector,
        routes: RouteConnector,
        staffing: StaffingConnector,
    ) -> None:
        self.stores_connector = stores
        self.sourcing_connector = sourcing
        self.inventory_connector = inventory
        self.routes_connector = routes
        self.staffing_connector = staffing

    def _visible(self, operation: str, call: Callable[[], T]) -> T:
        try:
            return call()
        except Exception as exc:
            raise InternalDataUnavailable(
                f"Internal operational data unavailable: {operation}: {exc}"
            ) from exc

    def get_stores(self, as_of: datetime) -> list[Store]:
        return self._visible("stores", lambda: self.stores_connector.get_stores(as_of))

    def get_store_sku_lanes(self, as_of: datetime) -> list[StoreSkuSourcingLane]:
        return self._visible("sourcing", lambda: self.sourcing_connector.get_store_sku_lanes(as_of))

    def get_inventory(self, as_of: datetime) -> list[InventorySnapshot]:
        return self._visible("inventory", lambda: self.inventory_connector.get_inventory(as_of))

    def get_routes(self, as_of: datetime) -> list[RouteLane]:
        return self._visible("routes", lambda: self.routes_connector.get_routes(as_of))

    def get_staffing_window(self, start: datetime, end: datetime) -> list[StaffingShift]:
        return self._visible("staffing", lambda: self.staffing_connector.get_staffing_window(start, end))


def _flag(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


class FixtureOperationalRepository:
    def __init__(self, version: str = "v1") -> None:
        self.fixtures = FixtureRepository(version)

    def get_stores(self, as_of: datetime) -> list[Store]:
        return self.fixtures.stores()

    def get_store_sku_lanes(self, as_of: datetime) -> list[StoreSkuSourcingLane]:
        return self.fixtures.sourcing_lanes()

    def get_inventory(self, as_of: datetime) -> list[InventorySnapshot]:
        return [
            InventorySnapshot(
                location_id=row["DC ID"],
                sku=row["UPC"],
                on_hand=row["On Hand"],
                committed=row["Committed"],
                safety_stock=row["Safety Stock"],
                eligible_inbound_before_cutoff=row["Eligible Inbound Before Cutoff"],
                atp=row["ATP"],
                captured_at=datetime.fromisoformat(str(row["Captured At"])),
            )
            for row in self.fixtures.table("dc_inventory").to_dict("records")
        ]

    def get_routes(self, as_of: datetime) -> list[RouteLane]:
        return [
            RouteLane(
                dc_id=row["DC ID"],
                store_id=row["Store ID"],
                eta_hours=row["ETA Hours"],
                eligible=_flag(row["Eligible"]),
                route_counties=tuple(
                    value.strip()
                    for value in str(row["Route Counties"]).split(";")
                    if value.strip()
                ),
            )
            for row in self.fixtures.table("routes").to_dict("records")
        ]

    def get_staffing_window(self, start: datetime, end: datetime) -> list[StaffingShift]:
        shifts: list[StaffingShift] = []
        for row in self.fixtures.table("staffing").to_dict("records"):
            if float(row["Scheduled Hours"]) <= 0:
                continue
            hour = {"AM": 8, "PM": 14, "Overnight": 22}.get(str(row["Shift"]), 8)
            shift_start = start.replace(hour=hour, minute=0, second=0, microsecond=0)
            if shift_start < start:
                shift_start += timedelta(days=1)
            shift_end = shift_start + timedelta(hours=float(row["Scheduled Hours"]))
            if shift_end <= end:
                shifts.append(
                    StaffingShift(
                        store_id=row["Store ID"],
                        employee_token=row["Employee Token"],
                        shift_start=shift_start,
                        shift_end=shift_end,
                        scheduled_hours=row["Scheduled Hours"],
                        on_call_eligible=_flag(row["On-call Eligible"]),
                    )
                )
        return shifts


def require_operational_repository(
    *,
    mode: str,
    connected: ConnectedOperationalRepository | None = None,
    fixture_version: str = "v1",
) -> OperationalDataRepository:
    if mode == "fixture":
        return FixtureOperationalRepository(fixture_version)
    if mode == "connected" and connected is not None:
        return connected
    raise InternalDataUnavailable(
        "Internal operational data unavailable: connected mode was requested but connectors are not configured"
    )


def default_staffing_window(as_of: datetime) -> tuple[datetime, datetime]:
    return as_of, as_of + timedelta(hours=48)
