from __future__ import annotations

import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_intelligence.collectors.live_bls import build_cpi_signal
from market_intelligence.llm.validation import validate_executive_brief
from market_intelligence.recalls.parsing import adjust_recall_score, normalize_recall_classification
from market_intelligence.signals.retail_context import signal_display_label


def test_recall_classification_is_exact_not_substring() -> None:
    assert normalize_recall_classification("Class I") == "Class I"
    assert normalize_recall_classification("Class II") == "Class II"
    assert normalize_recall_classification("Class III") == "Class III"
    assert adjust_recall_score(5.0, "Class II", "Terminated") == 4.8


def test_cpi_yoy_uses_same_calendar_month() -> None:
    payload = {
        "Results": {
            "series": [{
                "data": [
                    {"year": "2025", "period": "M08", "periodName": "August", "value": "100"},
                    {"year": "2025", "period": "M09", "periodName": "September", "value": "101"},
                    {"year": "2026", "period": "M08", "periodName": "August", "value": "110"},
                ]
            }]
        }
    }
    signal, table = build_cpi_signal(payload, "Headline CPI", "CPI", "Retailer")
    assert signal is not None
    august = table.loc[(table["year"] == 2026) & (table["period"] == "M08")].iloc[0]
    assert round(float(august["cpi_yoy_change_pct"]), 2) == 10.0


def test_signal_display_label_is_unique_for_duplicate_areas() -> None:
    rows = pd.DataFrame([
        {"signal_area": "Weather Risk", "signal_name": "storm_a", "source": "NOAA"},
        {"signal_area": "Weather Risk", "signal_name": "storm_b", "source": "NOAA"},
    ])
    labels = [signal_display_label(row, i) for i, (_, row) in enumerate(rows.iterrows())]
    assert len(set(labels)) == 2


def test_executive_brief_validator_rejects_template_success() -> None:
    valid = """EXECUTIVE SUMMARY\nWeather disruption may affect routes.\n\nTOP INSIGHTS\n- Weather Risk — NOAA — High: severe alert.\n- Inflation — BLS — Medium: CPI pressure.\n- Product Recalls — openFDA — High: food safety.\n\nPLANNING RELEVANCE\n- Review staffing and routes.\n\nRECOMMENDED ACTIONS\n- Validate store exposure.\n- Review DC inventory.\n- Refresh before order cut-off.\n\nCONFIDENCE AND LIMITATIONS\nExternal signals only."""
    invalid = valid.replace("Weather disruption may affect routes.", "<2 concise sentences>")
    assert validate_executive_brief(valid)[0]
    assert not validate_executive_brief(invalid)[0]
