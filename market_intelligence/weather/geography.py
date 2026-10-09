from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class StoreMatchDecision:
    store_id: str
    matched: bool
    method: str
    confidence: str
    store_county_fips: str
    alert_identifiers: tuple[str, ...]
    explanation: str


@dataclass(frozen=True)
class GeographyMatch:
    method: str
    confidence: str
    affected_store_ids: tuple[str, ...]
    evaluated_store_ids: tuple[str, ...]
    evidence_code: str
    explanation: str
    store_decisions: tuple[StoreMatchDecision, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "confidence": self.confidence,
            "affected_store_ids": list(self.affected_store_ids),
            "evaluated_store_ids": list(self.evaluated_store_ids),
            "evidence_code": self.evidence_code,
            "explanation": self.explanation,
            "store_decisions": [asdict(decision) for decision in self.store_decisions],
        }


def _county_pattern(county: str) -> re.Pattern[str] | None:
    county = str(county or "").strip()
    if not county:
        return None
    base = re.sub(r"\s+County$", "", county, flags=re.IGNORECASE).strip()
    if not base:
        return None
    words = r"\s+".join(re.escape(word) for word in base.split())
    return re.compile(rf"\b{words}(?:\s+County)?\b", flags=re.IGNORECASE)


def _point_in_ring(longitude: float, latitude: float, ring: Sequence[Sequence[float]]) -> bool:
    inside = False
    if len(ring) < 3:
        return False
    previous = ring[-1]
    for current in ring:
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        intersects = ((y1 > latitude) != (y2 > latitude)) and (
            longitude < (x2 - x1) * (latitude - y1) / ((y2 - y1) or 1e-12) + x1
        )
        if intersects:
            inside = not inside
        previous = current
    return inside


def _point_in_geometry(longitude: float, latitude: float, geometry: Mapping[str, Any]) -> bool:
    geometry_type = str(geometry.get("type") or "")
    coordinates = geometry.get("coordinates") or []
    polygons = [coordinates] if geometry_type == "Polygon" else coordinates if geometry_type == "MultiPolygon" else []
    for polygon in polygons:
        if not polygon:
            continue
        outer = polygon[0]
        holes = polygon[1:]
        if _point_in_ring(longitude, latitude, outer) and not any(
            _point_in_ring(longitude, latitude, hole) for hole in holes
        ):
            return True
    return False


def _alert_fips(geocodes: Mapping[str, Any] | None) -> tuple[str, ...]:
    identifiers: list[str] = []
    for values in (geocodes or {}).values():
        if not isinstance(values, (list, tuple)):
            continue
        for value in values:
            digits = "".join(ch for ch in str(value) if ch.isdigit())
            if len(digits) >= 5:
                identifiers.append(digits[-5:])
    return tuple(dict.fromkeys(identifiers))


def _store_value(store: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in store and store[name] not in (None, ""):
            return store[name]
    return None


def _result(
    *,
    method: str,
    confidence: str,
    evidence_code: str,
    explanation: str,
    state_stores: Sequence[Mapping[str, Any]],
    matched_ids: tuple[str, ...],
    alert_ids: tuple[str, ...],
) -> GeographyMatch:
    decisions = tuple(
        StoreMatchDecision(
            store_id=str(_store_value(store, "Store ID", "store_id") or ""),
            matched=str(_store_value(store, "Store ID", "store_id") or "") in matched_ids,
            method=method,
            confidence=confidence,
            store_county_fips=str(_store_value(store, "FIPS", "county_fips") or ""),
            alert_identifiers=alert_ids,
            explanation=explanation,
        )
        for store in state_stores
    )
    return GeographyMatch(
        method=method,
        confidence=confidence,
        affected_store_ids=matched_ids,
        evaluated_store_ids=tuple(decision.store_id for decision in decisions),
        evidence_code=evidence_code,
        explanation=explanation,
        store_decisions=decisions,
    )


def match_alert_to_stores(
    area_description: str,
    state: str,
    stores: Iterable[Mapping[str, Any]],
    *,
    alert_geometry: Mapping[str, Any] | None = None,
    alert_geocodes: Mapping[str, Any] | None = None,
) -> GeographyMatch:
    """Match in priority order: polygon, county FIPS/UGC, then county-name text."""
    state_code = str(state or "").strip().upper()
    state_stores = [
        store
        for store in stores
        if str(_store_value(store, "State", "state") or "").strip().upper() in {x.strip() for x in state_code.split(",")}
    ]
    if not state_stores:
        return GeographyMatch(
            method="Not evaluated",
            confidence="None",
            affected_store_ids=(),
            evaluated_store_ids=(),
            evidence_code="STORE_COVERAGE_MISSING",
            explanation=f"No internal Store records are available for {state_code}.",
        )

    if alert_geometry:
        polygon_matches = tuple(
            str(_store_value(store, "Store ID", "store_id") or "")
            for store in state_stores
            if _store_value(store, "Longitude", "longitude") is not None
            and _store_value(store, "Latitude", "latitude") is not None
            and _point_in_geometry(
                float(_store_value(store, "Longitude", "longitude")),
                float(_store_value(store, "Latitude", "latitude")),
                alert_geometry,
            )
        )
        if polygon_matches:
            return _result(
                method="NOAA polygon point intersection",
                confidence="High",
                evidence_code="MATCH_ALERT_POLYGON",
                explanation="Store coordinates intersect the NOAA alert polygon.",
                state_stores=state_stores,
                matched_ids=polygon_matches,
                alert_ids=("geometry:GeoJSON",),
            )

        missing_coordinates = [store for store in state_stores if _store_value(store, "Longitude", "longitude") is None or _store_value(store, "Latitude", "latitude") is None]
        if str(alert_geometry.get("type")) in {"Polygon", "MultiPolygon"} and not missing_coordinates:
            return _result(method="NOAA polygon point intersection", confidence="High",
                evidence_code="NO_STORE_INTERSECTION", explanation="No valid Store coordinate intersects the supplied NOAA polygon; county text does not override polygon exclusion.",
                state_stores=state_stores, matched_ids=(), alert_ids=("geometry:GeoJSON",))

        if str(alert_geometry.get("type")) in {"Polygon", "MultiPolygon"} and missing_coordinates:
            state_stores = missing_coordinates
    alert_fips = _alert_fips(alert_geocodes)
    if alert_fips:
        fips_matches = tuple(
            str(_store_value(store, "Store ID", "store_id") or "")
            for store in state_stores
            if str(_store_value(store, "FIPS", "county_fips") or "") in alert_fips
        )
        if fips_matches:
            return _result(
                method="NOAA UGC/FIPS join",
                confidence="Medium",
                evidence_code="MATCH_ALERT_FIPS",
                explanation="Store county FIPS matches a NOAA geocode identifier.",
                state_stores=state_stores,
                matched_ids=fips_matches,
                alert_ids=alert_fips,
            )

    county_matches = [
        store
        for store in state_stores
        if (
            (pattern := _county_pattern(str(_store_value(store, "County", "county") or "")))
            and pattern.search(str(area_description or ""))
        )
    ]
    matched_ids = tuple(str(_store_value(store, "Store ID", "store_id") or "") for store in county_matches)
    if matched_ids:
        counties = sorted(str(_store_value(store, "County", "county")) for store in county_matches)
        return _result(
            method="County name fallback",
            confidence="Low",
            evidence_code="MATCH_COUNTY_NAME_FALLBACK",
            explanation=(
                "NOAA area-description text contains the Store county name(s): "
                + ", ".join(counties)
                + ". This is a text fallback, not a polygon/GIS intersection."
            ),
            state_stores=state_stores,
            matched_ids=matched_ids,
            alert_ids=alert_fips,
        )

    return _result(
        method=(
            "No geographic intersection"
            if alert_geometry or alert_fips
            else "County name fallback"
        ),
        confidence="High" if alert_geometry else "Low",
        evidence_code="NO_STORE_INTERSECTION",
        explanation=(
            "No Store matched the NOAA polygon, geocodes, or county-name fallback; "
            f"{len(state_stores)} Store records were evaluated in {state_code}."
        ),
        state_stores=state_stores,
        matched_ids=(),
        alert_ids=alert_fips,
    )
