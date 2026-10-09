from typing import List
import pandas as pd

from market_intelligence.demand.planning import DEMAND_SIGNAL_SCOPE_FIELDS
from market_intelligence.signals.risk import risk_band


def parse_lines(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def build_demand_signal_log(feature_df: pd.DataFrame) -> pd.DataFrame:
    """Convert Market Intelligence rows into a compact Demand Signal Log."""
    if feature_df is None or feature_df.empty:
        return pd.DataFrame()
    rows = []
    ordered = feature_df.copy()
    if "risk_score" in ordered.columns:
        ordered = ordered.sort_values("risk_score", ascending=False, kind="mergesort")
    for i, (_, row) in enumerate(ordered.iterrows(), start=1):
        rows.append(
            {
                "Signal ID": f"SIG-{i:03d}",
                "Signal Area": row.get("signal_area", "External Signal"),
                "Region": str(row.get("region", "") or "US"),
                "Level": risk_band(float(row.get("risk_score", 0) or 0)),
                "Source": row.get("source", ""),
                "Demand Direction": row.get("demand_direction", "Validate against internal demand data"),
                "Forecast Feature": row.get("forecast_feature", row.get("signal_name", "external_signal")),
                "Business Impact": row.get("business_impact", ""),
                "Recommended Action": row.get("recommended_action", ""),
                # Carried so the planning calculation can scope itself to the products
                # this signal actually implicates rather than the whole store.
                DEMAND_SIGNAL_SCOPE_FIELDS[0]: str(row.get("signal_name", "") or ""),
                DEMAND_SIGNAL_SCOPE_FIELDS[1]: str(row.get("top_event", "") or ""),
                DEMAND_SIGNAL_SCOPE_FIELDS[2]: row.get("catalog_categories", []),
            }
        )
    return pd.DataFrame(rows)
