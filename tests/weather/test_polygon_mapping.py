from market_intelligence.weather.geography import match_alert_to_stores


STORES = [
    {
        "Store ID": "101",
        "State": "TX",
        "County": "Dallas County",
        "FIPS": "48113",
        "Latitude": 32.7767,
        "Longitude": -96.7970,
    },
    {
        "Store ID": "108",
        "State": "TX",
        "County": "El Paso County",
        "FIPS": "48141",
        "Latitude": 31.7619,
        "Longitude": -106.4850,
    },
]


def test_store_point_inside_noaa_polygon_wins_over_text_fallback() -> None:
    polygon = {
        "type": "Polygon",
        "coordinates": [[[-97.2, 32.4], [-96.4, 32.4], [-96.4, 33.1], [-97.2, 33.1], [-97.2, 32.4]]],
    }

    result = match_alert_to_stores(
        "Broad Texas scope",
        "TX",
        STORES,
        alert_geometry=polygon,
    )

    assert result.affected_store_ids == ("101",)
    assert result.method == "NOAA polygon point intersection"
    assert result.confidence == "High"
    assert result.evidence_code == "MATCH_ALERT_POLYGON"
    assert result.store_decisions[0].alert_identifiers == ("geometry:GeoJSON",)
    assert not result.store_decisions[1].matched


def test_missing_geometry_uses_noaa_fips_before_county_text() -> None:
    stores_without_coordinates = [{key: value for key, value in store.items() if key not in {"Latitude", "Longitude"}} for store in STORES]

    result = match_alert_to_stores(
        "No county name here",
        "TX",
        stores_without_coordinates,
        alert_geocodes={"SAME": ["048141"]},
    )

    assert result.affected_store_ids == ("108",)
    assert result.method == "NOAA UGC/FIPS join"
    assert result.confidence == "Medium"
    assert result.store_decisions[1].store_county_fips == "48141"
    assert result.store_decisions[1].alert_identifiers == ("48141",)


def test_polygon_with_missing_store_coordinates_falls_back_to_fips() -> None:
    polygon = {
        "type": "Polygon",
        "coordinates": [[[-107, 31], [-106, 31], [-106, 32], [-107, 32], [-107, 31]]],
    }
    store = {key: value for key, value in STORES[1].items() if key not in {"Latitude", "Longitude"}}

    result = match_alert_to_stores(
        "El Paso County",
        "TX",
        [store],
        alert_geometry=polygon,
        alert_geocodes={"SAME": ["048141"]},
    )

    assert result.method == "NOAA UGC/FIPS join"
    assert result.affected_store_ids == ("108",)
