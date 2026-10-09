from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from market_intelligence.collectors.noaa import normalize_noaa_alert
from market_intelligence.weather.impact import ProductRequirement, evaluate_weather_impact


ROOT = Path(__file__).resolve().parents[1] / "market_intelligence"


def test_domain_collectors_and_persistence_do_not_import_streamlit() -> None:
    protected = [ROOT / "collectors", ROOT / "weather", ROOT / "recalls", ROOT / "persistence", ROOT / "models"]
    for directory in protected:
        for path in directory.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            imports.update(
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            )
            assert "streamlit" not in imports, path


def test_vertical_weather_slice_from_noaa_to_product_allocation() -> None:
    alert = normalize_noaa_alert(
        {
            "id": "NWS-1",
            "properties": {
                "event": "Flood Watch",
                "areaDesc": "Dallas County, TX",
                "headline": "Flood Watch for Dallas",
                "onset": "2026-09-23T18:00:00+00:00",
                "ends": "2026-09-24T06:00:00+00:00",
                "geocode": {"SAME": ["048113"]},
            },
        },
        state="TX",
    )
    result = evaluate_weather_impact(
        alert=alert,
        stores=[{"Store ID": "108", "State": "TX", "County": "Dallas County"}],
        requirements=[
            ProductRequirement(
                store_id="108",
                sku="WATER",
                forecast_shortfall=22.6,
                case_pack=6,
                moq=0,
                primary={"dc_id": "DC-1", "dc_name": "Dallas DC", "atp": 0, "eta_hours": 2, "eligible": True},
                alternates=[{"dc_id": "DC-2", "dc_name": "Fort Worth DC", "atp": 24, "eta_hours": 4, "eligible": True}],
            )
        ],
        evaluated_at=datetime(2026, 9, 23, 12, tzinfo=timezone.utc),
    )

    assert result.geography.affected_store_ids == ("108",)
    assert result.allocations[0].required_quantity == 24
    assert result.allocations[0].backup_dc_name == "Fort Worth DC"
