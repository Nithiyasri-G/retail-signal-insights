from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from market_intelligence.models.alerts import WeatherAlert


def normalize_noaa_alert(record: Mapping[str, Any], *, state: str) -> WeatherAlert:
    properties = record.get("properties") or {}
    return WeatherAlert(
        alert_id=str(record.get("id") or properties.get("id") or ""),
        event=str(properties.get("event") or ""),
        state=state.strip().upper(),
        area_description=str(properties.get("areaDesc") or ""),
        headline=str(properties.get("headline") or ""),
        starts_at=datetime.fromisoformat(str(properties.get("onset") or properties.get("effective")).replace("Z", "+00:00")),
        ends_at=datetime.fromisoformat(str(properties.get("ends") or properties.get("expires")).replace("Z", "+00:00")),
        source="NOAA Weather Alerts",
        geometry=record.get("geometry"),
        geocodes=dict(properties.get("geocode") or {}),
    )
