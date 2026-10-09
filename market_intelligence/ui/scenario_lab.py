from __future__ import annotations

from typing import Any, Iterable, Mapping


def scenario_banner() -> str:
    return "Synthetic scenario - not an operational incident and not live NOAA/openFDA data."


def live_incidents_only(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [row for row in rows if not bool(int(row.get("Demo", 0) or 0))]
