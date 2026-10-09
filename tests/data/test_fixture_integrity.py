from __future__ import annotations

import math
from datetime import datetime, timezone

from market_intelligence.data.fixture_repository import FixtureRepository


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def test_manifest_and_primary_keys_are_consistent() -> None:
    repository = FixtureRepository("v1")
    stores = repository.table("stores")
    products = repository.table("products")
    dcs = repository.table("dc_network")

    assert repository.manifest["provenance"] == "synthetic"
    assert repository.manifest["files"]["stores.csv"]["rows"] == len(stores)
    assert stores["Store ID"].is_unique
    assert products["UPC"].is_unique
    assert dcs["DC ID"].is_unique
    assert (products["Case Pack"] > 0).all()


def test_fixture_foreign_keys_and_store_sku_completeness() -> None:
    repository = FixtureRepository("v1")
    stores = repository.table("stores")
    products = repository.table("products")
    dcs = repository.table("dc_network")
    store_inventory = repository.table("store_inventory")
    dc_inventory = repository.table("dc_inventory")
    lanes = repository.table("store_sku_dc_lanes")

    assert set(store_inventory["Store ID"]) <= set(stores["Store ID"])
    assert set(store_inventory["UPC"]) <= set(products["UPC"])
    assert set(dc_inventory["DC ID"]) <= set(dcs["DC ID"])
    assert set(lanes["DC ID"]) <= set(dcs["DC ID"])
    assert len(store_inventory) == len(stores) * len(products)
    assert not store_inventory.duplicated(["Store ID", "UPC"]).any()


def test_inventory_timestamps_are_utc_and_typed_records_load() -> None:
    repository = FixtureRepository("v1")
    for table_name in ("store_inventory", "dc_inventory"):
        timestamps = repository.table(table_name)["Captured At"]
        for raw in timestamps:
            parsed = datetime.fromisoformat(str(raw))
            assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)

    assert len(repository.stores()) == 16
    assert len(repository.products()) == 21
    assert len(repository.sourcing_lanes()) == 16 * 21 * 2


def test_store_fips_codes_are_real_not_sequential_placeholders() -> None:
    """W01: stores.csv previously numbered counties sequentially (48001-48008,
    13001-13008) instead of using real county FIPS codes, so a NOAA UGC/FIPS-coded
    alert for an unrelated county could falsely match these stores. Verify every store
    now carries its real county's FIPS code. Store 102 (Fort Worth) and Store 104
    (Arlington) legitimately share 48439 -- both are Tarrant County -- FIPS is a
    many-to-one county identifier, never treated as a per-store unique key anywhere in
    the codebase (only used as a set-membership match in weather/geography.py).
    """
    expected_fips = {
        "101": "48113",  # Dallas County, TX
        "102": "48439",  # Tarrant County, TX (Fort Worth)
        "103": "48201",  # Harris County, TX
        "104": "48439",  # Tarrant County, TX (Arlington)
        "105": "48085",  # Collin County, TX
        "106": "48453",  # Travis County, TX
        "107": "48029",  # Bexar County, TX
        "108": "48141",  # El Paso County, TX
        "201": "13121",  # Fulton County, GA
        "202": "13089",  # DeKalb County, GA
        "203": "13067",  # Cobb County, GA
        "204": "13051",  # Chatham County, GA
        "205": "13245",  # Richmond County, GA
        "206": "13021",  # Bibb County, GA
        "207": "13215",  # Muscogee County, GA
        "208": "13059",  # Clarke County, GA
    }
    repository = FixtureRepository("v1")
    stores = repository.table("stores")
    actual_fips = dict(zip(stores["Store ID"].astype(str), stores["FIPS"].astype(str)))
    assert actual_fips == expected_fips
    # Sequential placeholder pattern (480NN / 130NN matching row order) must be gone.
    assert not any(fips.startswith("4800") or fips.startswith("1300") for fips in actual_fips.values())


def test_primary_dc_assignment_is_diversified_by_product_category_not_uniform_per_store() -> None:
    """Real retail networks split sourcing by product/commodity lane -- different categories
    ship from different DCs network-wide -- not one single DC supplying every product at a
    store. The fixture previously had every store sourcing ~20 of its 21 products from one
    DC with only a single hand-picked exception, which read as unrealistic and made the
    Primary DC column look like a per-store constant rather than a per-product fact.
    """
    repository = FixtureRepository("v1")
    lanes = repository.table("store_sku_dc_lanes")
    primary = lanes[lanes["Service Level"] == "primary"]
    for store_id, group in primary.groupby("Store ID"):
        dc_counts = group["DC ID"].value_counts()
        # At least two distinct DCs are used as Primary for different products at every
        # store, and no single DC accounts for more than 90% of that store's products.
        assert len(dc_counts) >= 2, f"Store {store_id} has only one Primary DC across all products"
        assert dc_counts.max() / dc_counts.sum() <= 0.9, f"Store {store_id} is still dominated by one Primary DC"


def test_route_transit_hours_are_bounded_by_real_world_distance() -> None:
    """Fixture realism: routes.csv previously showed grossly unrealistic transit
    times unrelated to actual DC-to-store distance (e.g. Marietta at 9.0h for a
    ~20-mile haul; El Paso at 3.0h for a ~570-mile haul). This is a broad sanity
    bound, not an exact formula check, so legitimate handling/traffic variation
    stays possible: a truck cannot realistically average more than ~80 mph
    end-to-end (bounding transit from below), and a short haul should not show an
    extreme multi-hour transit time (bounding transit from above).
    """
    repository = FixtureRepository("v1")
    stores = repository.table("stores")
    routes = repository.table("routes")
    store_coords = {
        str(row["Store ID"]): (float(row["Latitude"]), float(row["Longitude"]))
        for _, row in stores.iterrows()
    }
    # DC coordinates approximated from a real store located in the DC's own county
    # (documented, grounded choice -- see data/fixtures/v1 fixture-generation notes),
    # not city-name intuition.
    dc_coords = {
        "DC-TX1": store_coords["101"],  # Dallas County, TX
        "DC-TX2": store_coords["102"],  # Tarrant County, TX
        "DC-GA1": store_coords["201"],  # Fulton County, GA
        "DC-GA2": store_coords["206"],  # Bibb County, GA
    }
    max_plausible_avg_mph = 80.0
    min_handling_hours = 1.0
    for _, row in routes.iterrows():
        dc_id = row["DC ID"]
        store_id = str(row["Store ID"])
        transit_hours = float(row["Transit Hours"])
        dlat, dlon = dc_coords[dc_id]
        slat, slon = store_coords[store_id]
        distance_miles = _haversine_miles(dlat, dlon, slat, slon)

        # Lower bound: even at an unrealistically fast average speed plus zero
        # handling time, transit cannot be faster than distance / max speed.
        min_plausible_hours = distance_miles / max_plausible_avg_mph
        assert transit_hours >= min_plausible_hours - 0.05, (
            f"{dc_id}->{store_id}: {transit_hours}h is faster than physically "
            f"plausible for {distance_miles:.0f} real miles"
        )

        # Upper bound: a short haul (<50 real miles) should not show an extreme
        # multi-hour transit time -- generous ceiling to allow handling/traffic
        # variation without pinning to one formula.
        if distance_miles < 50:
            assert transit_hours <= min_handling_hours + 3.0, (
                f"{dc_id}->{store_id}: {transit_hours}h is implausibly long for a "
                f"{distance_miles:.0f}-mile short haul"
            )
        # A long haul (>400 real miles) should not show an implausibly tiny
        # transit time.
        if distance_miles > 400:
            assert transit_hours > 4.0, (
                f"{dc_id}->{store_id}: {transit_hours}h is implausibly short for a "
                f"{distance_miles:.0f}-mile long haul"
            )
