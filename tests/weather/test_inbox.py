from __future__ import annotations

from market_intelligence.weather.inbox import (
    alert_identity_tokens,
    build_weather_inbox_row,
    referenced_alert_identity_tokens,
    shorten,
)


def _incident(
    incident_id: str,
    headline: str,
    area: str,
    starts_at: str,
    ends_at: str,
    *,
    status: str = "insufficient_evidence",
    stores: list[str] | None = None,
) -> dict:
    stores = stores or []
    return {
        "Incident ID": incident_id,
        "Source": "NOAA Weather Alerts",
        "Event": "Flood Watch",
        "Priority": "High" if stores else "Medium",
        "Status": status,
        "Store IDs": stores,
        "Decision Status": "Proposed",
        "Affected Scope": "1 store in TX" if stores else "TX state scope",
        "Evidence": {
            "region": "TX",
            "alert": {
                "headline": headline,
                "area_desc": area,
                "onset": starts_at,
                "ends": ends_at,
            },
        },
        "Scenario": {
            "status": status,
            "match_precision": (
                "County name fallback" if stores else "State only (broad scope)"
            ),
            "reason": (
                "Matched Store county by county-name fallback."
                if stores
                else "No Store county intersects the NOAA alert geography."
            ),
            "evidence_code": (
                "MATCH_COUNTY_NAME_FALLBACK" if stores else "NO_STORE_INTERSECTION"
            ),
            "candidate_stores": [{"Store ID": str(i)} for i in range(101, 109)],
        },
    }


def test_same_event_rows_have_distinct_business_identity() -> None:
    """Catches an inbox regression that makes different Flood Watches look identical."""
    incidents = [
        _incident(
            "WX-MIDLAND",
            "Flood Watch issued by NWS Midland/Odessa TX",
            "Guadalupe Mountains; Eddy County; Lea County; Culberson County",
            "2026-09-22T12:00:00-06:00",
            "2026-09-24T06:00:00-06:00",
        ),
        _incident(
            "WX-HUDSPETH",
            "Flood Watch issued by NWS El Paso Tx/Santa Teresa NM",
            "Salt Basin; Southern Hudspeth Highlands",
            "2026-09-22T12:00:00-06:00",
            "2026-09-23T19:00:00-06:00",
        ),
        _incident(
            "WX-ELPASO",
            "Flood Watch issued by NWS El Paso Tx/Santa Teresa NM",
            "Western El Paso County; Eastern/Central El Paso County",
            "2026-09-22T12:00:00-06:00",
            "2026-09-23T19:00:00-06:00",
            status="ok",
            stores=["108"],
        ),
    ]

    rows = [build_weather_inbox_row(incident) for incident in incidents]
    identities = {
        (row.headline, row.alert_area, row.starts_at, row.ends_at) for row in rows
    }

    assert len(identities) == 3
    assert {row.event for row in rows} == {"Flood Watch"}
    assert {row.state for row in rows} == {"TX"}


def test_inbox_row_explains_mapping_and_missing_evidence() -> None:
    """Catches loss of the mapping result and reason code in the inbox projection."""
    unmatched = build_weather_inbox_row(
        _incident(
            "WX-HUDSPETH",
            "Flood Watch issued by NWS El Paso Tx/Santa Teresa NM",
            "Salt Basin; Southern Hudspeth Highlands",
            "2026-09-22T12:00:00-06:00",
            "2026-09-23T19:00:00-06:00",
        )
    )
    matched = build_weather_inbox_row(
        _incident(
            "WX-ELPASO",
            "Flood Watch issued by NWS El Paso Tx/Santa Teresa NM",
            "Western El Paso County; Eastern/Central El Paso County",
            "2026-09-22T12:00:00-06:00",
            "2026-09-23T19:00:00-06:00",
            status="ok",
            stores=["108"],
        )
    )

    assert unmatched.mapping_result == "No Store intersection; 8 Stores evaluated"
    assert unmatched.evidence_code == "NO_STORE_INTERSECTION"
    assert matched.mapping_result == "1 Store matched: 108"
    assert matched.evidence_code == "MATCH_COUNTY_NAME_FALLBACK"
    assert matched.window == "2026-09-22 18:00 UTC → 2026-09-24 01:00 UTC"


def test_inbox_fallbacks_cover_timing_mapping_and_evidence_states() -> None:
    base = {
        "Incident ID": "WX-X",
        "Type": "Weather",
        "Evidence": {"alert": {"effective": "not-a-date", "expires": ""}},
        "Scenario": {},
    }
    row = build_weather_inbox_row(base)
    assert row.window == "not-a-date"
    assert row.mapping_result == "Store mapping unavailable"
    assert row.evidence_code == "EVIDENCE_UNCLASSIFIED"

    coverage = build_weather_inbox_row(
        {**base, "Status": "no_match", "Scenario": {"reason": "No demonstration store exists"}}
    )
    comparable = build_weather_inbox_row(
        {**base, "Status": "insufficient_evidence", "Scenario": {"reason": "Only 2 comparable cases"}}
    )
    derived_match = build_weather_inbox_row(
        {**base, "Status": "ok", "Store IDs": ["1", "2"], "Scenario": {"match_precision": "county text"}}
    )
    assert coverage.evidence_code == "STORE_COVERAGE_MISSING"
    assert comparable.evidence_code == "COMPARABLE_EVENTS_BELOW_THRESHOLD"
    assert derived_match.evidence_code == "MATCH_COUNTY_NAME_FALLBACK"
    assert derived_match.mapping_result == "2 Stores matched: 1, 2"
    assert shorten("abc", 5) == "abc"
    assert shorten("abcdef", 5) == "abcd…"


def test_inbox_differentiates_noaa_update_with_business_changes() -> None:
    previous = _incident(
        "WX-OLD",
        "Flood Watch issued by NWS El Paso Tx/Santa Teresa NM",
        "Western El Paso County; Eastern/Central El Paso County",
        "2026-09-23T12:00:00+00:00",
        "2026-09-23T20:00:00+00:00",
        status="ok",
        stores=["108"],
    )
    previous["Evidence"]["alert"].update(
        {
            "alert_id": "urn:old",
            "sender_name": "NWS El Paso Tx/Santa Teresa NM",
            "severity": "Severe",
            "urgency": "Future",
            "certainty": "Possible",
            "sent": "2026-09-23T12:00:00+00:00",
            "geocode": {"UGC": ["TXZ418", "TXZ419"]},
            "source_query_states": ["TX"],
        }
    )

    current = _incident(
        "WX-NEW",
        "Flood Watch issued by NWS El Paso Tx/Santa Teresa NM",
        "Western El Paso County; Eastern/Central El Paso County; Salt Basin",
        "2026-09-23T12:00:00+00:00",
        "2026-09-24T01:00:00+00:00",
        status="ok",
        stores=["108", "109"],
    )
    current["Evidence"]["alert"].update(
        {
            "alert_id": "urn:new",
            "sender_name": "NWS El Paso Tx/Santa Teresa NM",
            "severity": "Severe",
            "urgency": "Future",
            "certainty": "Possible",
            "sent": "2026-09-23T18:00:00+00:00",
            "message_type": "Update",
            "references": [{"identifier": "urn:old", "sent": "2026-09-23T12:00:00+00:00"}],
            "geocode": {"UGC": ["NMZ401", "TXZ418", "TXZ419"]},
            "source_query_states": ["TX"],
        }
    )

    row = build_weather_inbox_row(current, previous)
    assert row.issuing_office == "NWS El Paso Tx/Santa Teresa NM"
    assert row.geography.startswith("NM + TX · Western El Paso County; Eastern/Central El Paso County")
    assert row.noaa_profile == "Severe · Future · Possible"
    assert row.store_scope == "2 Stores · 108, 109"
    assert row.store_scope_change == "+1 (109)"
    assert "Area +1 area" in row.what_changed
    assert "End extended to 24 Sep 01:00 UTC" in row.what_changed
    assert row.validity == "Issued 23 Sep 18:00 · until 24 Sep 01:00 UTC"
    assert row.mapping_method == "County fallback"


def test_inbox_update_without_local_predecessor_is_explicit() -> None:
    incident = _incident(
        "WX-UPDATE",
        "Flood Watch issued by NWS El Paso Tx/Santa Teresa NM",
        "Western El Paso County; Eastern/Central El Paso County",
        "2026-09-23T12:00:00+00:00",
        "2026-09-24T01:00:00+00:00",
        status="ok",
        stores=["108"],
    )
    incident["Evidence"]["alert"].update(
        {
            "message_type": "Update",
            "references": [{"identifier": "urn:prior"}],
            "severity": "Severe",
            "urgency": "Future",
            "certainty": "Possible",
        }
    )
    row = build_weather_inbox_row(incident)
    assert row.what_changed == "NOAA update · prior content not in local history"
    assert row.store_scope_change == "Prior scope unavailable"


def test_noaa_reference_identity_matches_url_and_urn_forms() -> None:
    alert = {
        "alert_id": "https://api.weather.gov/alerts/urn:oid:current",
        "references": [
            {
                "@id": "https://api.weather.gov/alerts/urn:oid:previous",
                "identifier": "urn:oid:previous",
                "sent": "2026-09-23T12:00:00+00:00",
            }
        ],
    }
    assert alert_identity_tokens(alert) == (
        "https://api.weather.gov/alerts/urn:oid:current",
        "urn:oid:current",
    )
    assert referenced_alert_identity_tokens(alert) == (
        "https://api.weather.gov/alerts/urn:oid:previous",
        "urn:oid:previous",
    )
