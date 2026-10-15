from typing import Any
from typing import Dict
from typing import List
import json
import pandas as pd

from market_intelligence.config.settings import NVIDIA_MAX_ARTICLES, NVIDIA_MAX_FEATURE_ROWS
from market_intelligence.signals.risk import risk_band


def build_llm_payload(feature_df: pd.DataFrame, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "features": feature_df.to_dict(orient="records")[:NVIDIA_MAX_FEATURE_ROWS],
        "articles": articles[:NVIDIA_MAX_ARTICLES],
    }


def llm_system_prompt() -> str:
    return (
        "You are a retail market intelligence analyst. Ground your answer only in the supplied signals. "
        "Write concise executive guidance for buyers, category managers, demand planners, and supply-chain teams."
    )


def llm_user_prompt(retailer: str, region: str, payload: Dict[str, Any]) -> str:
    return f"""
    Retailer: {retailer}
    Region: {region}

    Signal payload:
    {json.dumps(payload, indent=2, default=str)[:9000]}

    Produce:
    1. Executive summary in 2-3 sentences
    2. Top 3 insights, and for each one cite signal_area, source, risk level (High/Medium/Low), score_reason, retail_category, planning_owner, and demand_direction
    3. Retail category impact, clearly stating how the signal can become a forecast feature
    4. Recommended buyer/category/demand-planning/supply-chain/compliance actions
    5. Confidence and limitations

    Rules:
    - Ground every claim only in the supplied payload.
    - Do not invent internal sales, POS, inventory, margin, or category-performance facts.
    - If a signal is missing, say it is missing instead of estimating it.
    - Explain why each High/Medium/Low level matters; do not expose the underlying numeric score.
    - Use retailer-neutral business language when the payload includes category, KPI, planning owner, impact hypothesis, and internal-data requirements.
    """


def _compact_signal_for_nvidia(row: pd.Series) -> Dict[str, Any]:
    """
    Keep only the business fields NVIDIA needs to understand one signal.
    The numeric score is used internally to derive High/Medium/Low but is
    not exposed to the final business-facing brief.
    """
    fields = [
        "signal_area",
        "source",
        "score_reason",
        "business_impact",
        "recommended_action",
        "confidence",
        "enterprise_kpi",
        "planning_owner",
        "demand_direction",
        "impact_hypothesis",
        "internal_data_needed",
        "forecast_feature",
        "latest_value",
        "mom_pct",
        "yoy_pct",
        "region",
        "fetched_count",
        "operationally_relevant_fetched_count",
    ]

    compact: Dict[str, Any] = {}
    for key in fields:
        if key not in row.index:
            continue
        value = row.get(key)
        try:
            if pd.isna(value):
                continue
        except Exception:
            pass
        compact[key] = value

    try:
        compact["signal_level"] = risk_band(float(row.get("risk_score", 0)))
    except Exception:
        compact["signal_level"] = "Unknown"

    return compact
