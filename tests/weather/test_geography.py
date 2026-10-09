from __future__ import annotations

from market_intelligence.weather.geography import match_alert_to_stores


TEXAS_STORES = [
    {"Store ID": "101", "State": "TX", "County": "Dallas County"},
    {"Store ID": "102", "State": "TX", "County": "Tarrant County"},
    {"Store ID": "103", "State": "TX", "County": "Harris County"},
    {"Store ID": "104", "State": "TX", "County": "Tarrant County"},
    {"Store ID": "105", "State": "TX", "County": "Collin County"},
    {"Store ID": "106", "State": "TX", "County": "Travis County"},
    {"Store ID": "107", "State": "TX", "County": "Bexar County"},
    {"Store ID": "108", "State": "TX", "County": "El Paso County"},
]


def test_county_name_fallback_maps_store_108_without_claiming_precision() -> None:
    """Catches county substring matches being presented as precise GIS matches."""
    result = match_alert_to_stores(
        "Western El Paso County; Eastern/Central El Paso County",
        "TX",
        TEXAS_STORES,
    )

    assert result.affected_store_ids == ("108",)
    assert result.evaluated_store_ids == tuple(str(i) for i in range(101, 109))
    assert result.method == "County name fallback"
    assert result.confidence == "Low"
    assert result.evidence_code == "MATCH_COUNTY_NAME_FALLBACK"
    assert "El Paso County" in result.explanation


def test_no_intersection_is_not_reported_as_candidate_impact() -> None:
    """Catches state-level Stores being described as affected candidates."""
    result = match_alert_to_stores(
        "Salt Basin; Southern Hudspeth Highlands; Rio Grande Valley of Eastern Hudspeth County",
        "TX",
        TEXAS_STORES,
    )

    assert result.affected_store_ids == ()
    assert len(result.evaluated_store_ids) == 8
    assert result.evidence_code == "NO_STORE_INTERSECTION"
    assert result.method == "County name fallback"
    assert "8 Store records were evaluated" in result.explanation


def test_missing_state_store_coverage_has_its_own_reason() -> None:
    """Catches missing internal coverage being confused with weak history."""
    result = match_alert_to_stores(
        "Maricopa County",
        "AZ",
        TEXAS_STORES,
    )

    assert result.affected_store_ids == ()
    assert result.evaluated_store_ids == ()
    assert result.evidence_code == "STORE_COVERAGE_MISSING"
    assert result.method == "Not evaluated"
    assert result.explanation == "No internal Store records are available for AZ."


def test_geography_serialization_and_blank_county_are_safe() -> None:
    result = match_alert_to_stores(
        "Dallas County, TX",
        "TX",
        [{"Store ID": "108", "State": "TX", "County": ""}],
    )

    assert result.affected_store_ids == ()
    assert result.to_dict()["evaluated_store_ids"] == ["108"]
