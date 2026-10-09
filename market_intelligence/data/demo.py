from market_intelligence.data.fixture_repository import FixtureRepository
from typing import Dict
from typing import Optional
from typing import Tuple
import hashlib
import pandas as pd


def demo_market_signals() -> pd.DataFrame:
    """Versioned synthetic market signals loaded from reviewed fixtures."""
    return FixtureRepository("v1").table("market_signals")


def demo_store_master() -> pd.DataFrame:
    """Versioned synthetic Store master loaded from reviewed fixtures."""
    return FixtureRepository("v1").table("stores")


def demo_dc_network() -> pd.DataFrame:
    """Versioned synthetic DC network loaded from reviewed fixtures."""
    return FixtureRepository("v1").table("dc_network")


def demo_route_network(store_master: Optional[pd.DataFrame] = None, dc_network: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Versioned synthetic Store/DC routes loaded from reviewed fixtures."""
    return FixtureRepository("v1").table("routes")


def demo_supplier_master() -> pd.DataFrame:
    """Versioned synthetic supplier master loaded from reviewed fixtures."""
    return FixtureRepository("v1").table("suppliers")


def demo_product_catalog() -> pd.DataFrame:
    """Versioned synthetic product catalog loaded from reviewed fixtures."""
    return FixtureRepository("v1").table("products")


def demo_lot_master(catalog: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Versioned synthetic lot master loaded from reviewed fixtures."""
    return FixtureRepository("v1").table("lots")


def _recall_table(name: str) -> pd.DataFrame:
    return FixtureRepository("recall_v1").table(name)


def demo_recall_catalog() -> pd.DataFrame:
    """Wider grocery-style catalog used only for recall matching (the 21 v1 items plus fictional private-label items)."""
    return _recall_table("products")


def demo_recall_lot_master() -> pd.DataFrame:
    return _recall_table("lots")


def demo_recall_store_inventory() -> pd.DataFrame:
    return _recall_table("store_inventory")


def demo_recall_dc_inventory() -> pd.DataFrame:
    return _recall_table("dc_inventory")


def demo_recall_sales_summary() -> pd.DataFrame:
    return _recall_table("sales_summary")


def demo_recall_customer_purchases() -> pd.DataFrame:
    return _recall_table("customer_purchases")


def demo_recall_substitute_products() -> pd.DataFrame:
    return _recall_table("substitutes")


def demo_recall_supplier_master() -> pd.DataFrame:
    return _recall_table("suppliers")


def _demo_seed(*parts: str) -> int:
    return int(hashlib.md5("-".join(str(p) for p in parts).encode()).hexdigest(), 16)


def demo_store_inventory(store_master: Optional[pd.DataFrame] = None, catalog: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Versioned synthetic Store/SKU snapshots loaded from reviewed fixtures."""
    return FixtureRepository("v1").table("store_inventory")


def demo_dc_inventory(dc_network: Optional[pd.DataFrame] = None, catalog: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Versioned synthetic DC/SKU ATP snapshots loaded from reviewed fixtures."""
    return FixtureRepository("v1").table("dc_inventory")


def demo_sales_summary(store_master: Optional[pd.DataFrame] = None, catalog: Optional[pd.DataFrame] = None, lot_master: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Versioned synthetic Store/SKU sales summary loaded from reviewed fixtures."""
    return FixtureRepository("v1").table("sales_summary")


def demo_customer_purchases(sales_summary: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Versioned synthetic customer-purchase summary loaded from reviewed fixtures."""
    return FixtureRepository("v1").table("customer_purchases")


def demo_substitute_products() -> pd.DataFrame:
    """Versioned synthetic substitute-product relationships loaded from reviewed fixtures."""
    return FixtureRepository("v1").table("substitutes")


def demo_staffing_summary(store_master: Optional[pd.DataFrame] = None, days: int = 14) -> pd.DataFrame:
    """Versioned synthetic staffing summary loaded from reviewed fixtures."""
    return FixtureRepository("v1").table("staffing_summary")


def demo_employee_schedule(store_master: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Versioned synthetic staffing schedule loaded from reviewed fixtures."""
    return FixtureRepository("v1").table("staffing")


def demo_zip_centroids() -> Dict[str, Tuple[float, float]]:
    """ZIP -> (latitude, longitude) lookup for the commute-distance estimate below.

    These are approximate demonstration centroids (see the fixture file), not
    authoritative geocoding data -- production should call a real geocoding service.
    """
    table = FixtureRepository("v1").table("zip_centroids")
    return {
        str(row["ZIP"]): (float(row["Latitude"]), float(row["Longitude"]))
        for row in table.to_dict("records")
    }


def scenario_weather_assumptions() -> pd.DataFrame:
    """Versioned synthetic weather assumptions loaded from reviewed fixtures."""
    return FixtureRepository("v1").table("weather_assumptions")
