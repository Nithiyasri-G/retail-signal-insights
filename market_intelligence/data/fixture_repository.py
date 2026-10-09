from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from market_intelligence.models.products import Product
from market_intelligence.models.stores import Store, StoreSkuSourcingLane


class FixtureRepository:
    """Read-only repository for versioned, reviewable synthetic POC fixtures."""

    def __init__(self, version: str = "v1", root: str | Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.root = Path(root) if root else project_root / "data" / "fixtures" / version
        self.version = version
        if not (self.root / "manifest.json").exists():
            raise FileNotFoundError(f"Fixture manifest not found: {self.root / 'manifest.json'}")

    @property
    def manifest(self) -> dict[str, Any]:
        return json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))

    def table(self, name: str) -> pd.DataFrame:
        path = self.root / f"{name}.csv"
        return pd.read_csv(
            path,
            dtype={"Store ID": str, "UPC": str, "FIPS": str},
        )

    def stores(self) -> list[Store]:
        return [
            Store(
                store_id=row["Store ID"],
                name=row["Store Name"],
                state=row["State"],
                county_fips=row["FIPS"],
                latitude=row["Latitude"],
                longitude=row["Longitude"],
                operating_status=row.get("Operating Status", "Open"),
            )
            for row in self.table("stores").to_dict("records")
        ]

    def products(self) -> list[Product]:
        return [
            Product(
                sku=row["UPC"],
                name=row["Product"],
                category=row["Category"],
                case_pack=row["Case Pack"],
                moq=row["MOQ"],
                sellable_unit=row["Sellable Unit"],
            )
            for row in self.table("products").to_dict("records")
        ]

    def sourcing_lanes(self) -> list[StoreSkuSourcingLane]:
        return [
            StoreSkuSourcingLane(
                store_id=row["Store ID"],
                sku=row["UPC"],
                dc_id=row["DC ID"],
                rank=row["Rank"],
                transit_hours=row["Transit Hours"],
                service_level=row["Service Level"],
                active=bool(row["Active"]),
            )
            for row in self.table("store_sku_dc_lanes").to_dict("records")
        ]
