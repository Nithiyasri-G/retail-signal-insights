from typing import Any
from typing import Dict
from typing import List
from typing import Optional
import pandas as pd

from market_intelligence.config.settings import APIFY_HARD_KEYWORD_LIMIT, APIFY_SAFE_TIME_RANGE
from market_intelligence.util.text import display_fallback_reason
from market_intelligence.signals.collector_evidence import any_mock_used, build_collector_evidence
from market_intelligence.signals.retail_context import enrich_feature_rows_for_retailer
from market_intelligence.signals.risk import risk_band


def compute_composite_scores(feature_df: pd.DataFrame) -> Dict[str, float]:
    if feature_df.empty:
        return {"Market Opportunity": 0.0, "Market Risk": 0.0, "Forecast Impact": 0.0}
    area_scores: Dict[str, float] = {}
    for _, row in feature_df.iterrows():
        if pd.isna(row.get("risk_score")):
            continue
        area = str(row["signal_area"])
        area_scores[area] = max(area_scores.get(area, 0.0), float(row["risk_score"]))
    opportunity = max(
        area_scores.get("Retail News", 0.0),
        area_scores.get("Search Demand", 0.0),
        area_scores.get("Category CPI", 0.0) * 0.7,
    )
    risk = max(
        area_scores.get("Product Recalls", 0.0),
        area_scores.get("Weather Risk", 0.0),
        area_scores.get("Inflation", 0.0),
        area_scores.get("Category CPI", 0.0),
    )
    impact = min(10.0, (opportunity * 0.45) + (risk * 0.45) + (len(feature_df) * 0.25))
    return {
        "Market Opportunity": round(opportunity, 2),
        "Market Risk": round(risk, 2),
        "Forecast Impact": round(impact, 2),
    }


def compute_retail_kpis(feature_df: pd.DataFrame) -> Dict[str, float]:
    """Headline KPI cards. Each card is the highest score among the signal areas it names.

    "Consumer Price Pressure" deliberately combines Headline CPI and every Category CPI
    row (food, household, gasoline), so it can be higher than the Headline-only
    "Value Basket Pressure" row in the Impact by retail category table.
    """
    if feature_df.empty:
        return {
            "Consumer Price Pressure": 0.0,
            "Safety and Compliance Risk": 0.0,
            "Supply Chain Disruption Risk": 0.0,
            "Demand Signal Priority": 0.0,
        }

    def max_for(column: str, values: List[str]) -> float:
        if column not in feature_df.columns:
            return float("nan")
        mask = feature_df[column].astype(str).isin(values)
        if not mask.any():
            return float("nan")
        return float(feature_df.loc[mask, "risk_score"].max())

    price_pressure = max_for("signal_area", ["Inflation", "Category CPI"])
    safety = max_for("signal_area", ["Product Recalls"])
    disruption = max_for("signal_area", ["Weather Risk"])
    demand = max_for("signal_area", ["Search Demand", "Retail News"])
    return {
        "Consumer Price Pressure": round(price_pressure, 2),
        "Safety and Compliance Risk": round(safety, 2),
        "Supply Chain Disruption Risk": round(disruption, 2),
        "Demand Signal Priority": round(demand, 2),
    }


def weather_exposure_note(row: Any) -> str:
    """Separate a state-level weather level from confirmed store exposure.

    The weather level reflects NOAA alert severity across the state. The collector also
    records how many fetched alerts match a store county in its screening check. That
    check covers the store master only; DC locations and delivery routes are not
    evaluated here, so the wording never claims "no DC exposure".
    """
    unrecorded = (
        "Store-footprint relevance was not recorded for this run. "
        "DC and route exposure has not been evaluated."
    )
    try:
        relevant = row.get("operationally_relevant_fetched_count")
        fetched = row.get("fetched_count")
        if pd.isna(relevant) or pd.isna(fetched):
            return unrecorded
        relevant, fetched = int(relevant), int(fetched)
    except (TypeError, ValueError):
        return unrecorded
    noun = "alert" if fetched == 1 else "alerts"
    if relevant == 0:
        return (
            f"This is a state-level signal. None of the {fetched} fetched {noun} matched a store county in the "
            "screening check. DC and route exposure is not evaluated here; see Operational Impact Center."
        )
    return (
        f"{relevant} of {fetched} fetched {noun} matched a store county in the screening check; "
        "validate store exposure in Operational Impact Center. DC and route exposure is not evaluated here."
    )


def build_trust_metrics(run: Dict[str, Any], feature_df: pd.DataFrame) -> List[Dict[str, str]]:
    results = run.get("results", {})
    llm_audit = run.get("llm_audit", {})
    evidence_records = build_collector_evidence(results, run.get("run_config", {}), llm_audit)
    live_sources = sum(1 for record in evidence_records if record.get("live_request_made") == "Yes")
    raw_records = sum(int(record.get("raw_records_pulled", 0) or 0) for record in evidence_records)
    mock_used = any_mock_used(results, llm_audit)
    brief_source = "Agent" if run.get("brief_source") == "nvidia" else "Rule-based"
    return [
        {"label": "External Signals", "value": "Mixed / mock" if mock_used else "Live", "note": "Collector evidence is audited separately from internal operations data.", "state": "warn" if mock_used else "good"},
        {"label": "Internal Operations", "value": "Synthetic", "note": "Versioned internal dataset for this proof of concept; not the client's ERP or WMS.", "state": "warn"},
        {"label": "Live Sources", "value": str(live_sources), "note": "Sources that made a live request or returned live evidence.", "state": "good"},
        {"label": "Raw Records", "value": str(raw_records), "note": "Inspectable source records behind the normalized rows.", "state": "good" if raw_records else "warn"},
        {"label": "Feature Rows", "value": str(len(feature_df)), "note": "Normalized external signal rows, ready to join to internal data.", "state": "good" if len(feature_df) else "warn"},
        {"label": "Brief Mode", "value": brief_source, "note": display_fallback_reason(llm_audit.get("fallback_reason") or "Agent brief grounded in the shown payload."), "state": "good" if brief_source == "Agent" else "warn"},
    ]


def build_previous_run_comparison(feature_df: pd.DataFrame, previous_run: Optional[Dict[str, Any]]) -> pd.DataFrame:
    if feature_df.empty or not previous_run:
        return pd.DataFrame()
    previous_df = previous_run.get("feature_df", pd.DataFrame())
    if previous_df is None or previous_df.empty:
        return pd.DataFrame()
    if "retail_category" not in previous_df.columns:
        previous_df = enrich_feature_rows_for_retailer(previous_df, str(previous_run.get("run_config", {}).get("retailer", "Retailer")))
    key_cols = [col for col in ["source", "signal_name", "region"] if col in feature_df.columns and col in previous_df.columns]
    if not key_cols:
        return pd.DataFrame()
    current_cols = key_cols + [col for col in ["signal_area", "retail_category", "planning_owner", "risk_score"] if col in feature_df.columns]
    previous_cols = key_cols + [col for col in ["risk_score"] if col in previous_df.columns]
    current = feature_df[current_cols].copy()
    previous = previous_df[previous_cols].copy()
    current = current.rename(columns={"risk_score": "current_score"})
    previous = previous.rename(columns={"risk_score": "previous_score"})
    merged = current.merge(previous, on=key_cols, how="left")
    if "previous_score" not in merged.columns:
        return pd.DataFrame()
    merged["previous_score"] = pd.to_numeric(merged["previous_score"], errors="coerce")
    merged["current_score"] = pd.to_numeric(merged["current_score"], errors="coerce")
    merged["delta"] = (merged["current_score"] - merged["previous_score"]).round(2)
    # Movement is judged on the visible High/Medium/Low level, not the hidden numeric
    # score, so a row can never read "Increased" while both levels show the same value.
    level_rank = {"Low": 0, "Medium": 1, "High": 2}

    def _movement(previous: Any, current: Any) -> str:
        if pd.isna(previous):
            return "New"
        before = level_rank.get(risk_band(float(previous)))
        after = level_rank.get(risk_band(float(current))) if not pd.isna(current) else None
        if before is None or after is None:
            return "Not evaluated"
        return "Increased" if after > before else "Decreased" if after < before else "Stable"

    merged["movement"] = [_movement(p, c) for p, c in zip(merged["previous_score"], merged["current_score"])]
    return merged.sort_values(["movement", "current_score"], ascending=[True, False])


def validation_guidance_for_row(row: pd.Series) -> Dict[str, str]:
    area = str(row.get("signal_area", "")).lower()
    feature = str(row.get("forecast_feature", "")).lower()
    if "recall" in area or "recall" in feature:
        return {
            "validation_analysis": "Match UPC/vendor/product text to SKU master, then compare affected-store inventory and substitution sales before and after recall date.",
            "validation_metric": "UPC match rate, exposed on-hand units, substitute category lift, withdrawal completion rate",
        }
    if "weather" in area or "weather" in feature:
        return {
            "validation_analysis": "Join alerts to store/DC geography and compare affected stores against unaffected stores for traffic, sales, and replenishment delays.",
            "validation_metric": "Affected vs control sales delta, late delivery count, emergency-category uplift",
        }
    if "cpi" in feature or "inflation" in area:
        return {
            "validation_analysis": "Join CPI pressure to weekly category sales, basket mix, unit velocity, and price changes to test trade-down behavior.",
            "validation_metric": "Category unit lift, average basket shift, price sensitivity, margin pressure",
        }
    if "trends" in feature or "search" in area:
        return {
            "validation_analysis": "Compare regional search interest against store traffic, sales velocity, and promotion calendar in the same region and week.",
            "validation_metric": "Search-to-sales lead correlation, regional conversion lift, promotion-adjusted demand",
        }
    if "news" in feature or "news" in area:
        return {
            "validation_analysis": "Tag event dates and compare category/store performance before and after the news event while controlling for promotions.",
            "validation_metric": "Pre/post category variance, forecast error reduction, event-attributed exception count",
        }
    return {
        "validation_analysis": "Join this external signal to internal POS, inventory, product hierarchy, promotion, and store data; test whether it explains forecast variance.",
        "validation_metric": "Forecast error reduction, category sales variance, inventory exception count",
    }


def build_internal_validation_plan(feature_df: pd.DataFrame) -> pd.DataFrame:
    if feature_df.empty:
        return pd.DataFrame()
    records = []
    for _, row in feature_df.sort_values("risk_score", ascending=False, kind="mergesort").iterrows():
        guidance = validation_guidance_for_row(row)
        records.append(
            {
                "external_signal": (
                    f"{row.get('signal_area', 'Signal')} / {row.get('signal_name', '')}"
                    + (f" ({row.get('region')})" if str(row.get("region", "") or "").strip() else "")
                ),
                "retail_category": row.get("retail_category", ""),
                "planning_owner": row.get("planning_owner", ""),
                "internal_data_needed": row.get("internal_data_needed", ""),
                "validation_analysis": guidance["validation_analysis"],
                "validation_metric": guidance["validation_metric"],
                "decision_use": "Promote to model feature if it improves forecast error or explains planning exceptions.",
            }
        )
    return pd.DataFrame(records)


def build_recommended_actions(feature_df: pd.DataFrame, results: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
    actions = []
    if feature_df.empty:
        return [{"label": "Setup", "title": "Run signal sources", "body": "Enable at least one source to generate normalized external signals."}]
    top_rows = feature_df.sort_values("risk_score", ascending=False, kind="mergesort").head(3)
    for _, row in top_rows.iterrows():
        owner = str(row.get("planning_owner") or "Planning Owner")
        category = str(row.get("retail_category") or row.get("signal_area") or "Category")
        hypothesis = str(row.get("impact_hypothesis") or row.get("recommended_action") or "Review this signal with the category owner.")
        title = category
        exposure = ""
        if str(row.get("signal_area", "")) == "Weather Risk":
            region = str(row.get("region", "") or "").strip()
            if region and region.upper() != "US":
                title = f"{category} \u2014 {region}"
            exposure = f" {weather_exposure_note(row)}"
        actions.append(
            {
                "label": owner,
                "title": title,
                "body": f"{hypothesis}{exposure} Next: {row.get('recommended_action', 'Review this signal with the category owner.')}",
            }
        )
    apify_result = results.get("apify")
    if apify_result and apify_result.get("status") in {"failed", "skipped"}:
        actions.append(
            {
                "label": "Apify",
                "title": "Search demand not collected",
                "body": apify_result.get("error") or "Search demand was not collected for this run. The remaining public sources are unaffected.",
            }
        )
    return actions[:4]


def scoring_formula_for_row(row: pd.Series) -> str:
    source = str(row.get("source", "")).lower()
    area = str(row.get("signal_area", "")).lower()
    if "bls" in source or "cpi" in area:
        return "CPI scoring starts from a neutral 4.0, adjusts upward or downward using monthly CPI change, adds pressure when YoY inflation is elevated, then clips to a 1-10 range."
    if "fda" in source or "recall" in area:
        return "Recall scoring starts from reason severity, then adjusts for FDA classification and recall status. Class I and ongoing recalls increase the score; terminated recalls reduce it."
    if "noaa" in source or "weather" in area:
        return "Weather scoring sums active NOAA alert severity weights for the selected state, adds urgency/certainty pressure, then caps the supply-chain risk score at 10."
    if "gnews" in source or "news" in area:
        return "News scoring classifies each article into event type and sentiment, adjusts for recency/source quality, then averages deduplicated article risk."
    if "apify" in source or "search" in area:
        return f"Search scoring uses top regional Google Trends interest divided by 10, with backend limits of {APIFY_SAFE_TIME_RANGE} and {APIFY_HARD_KEYWORD_LIMIT} keyword(s)."
    return "Score is normalized to a 1-10 signal intensity scale using the collector-specific scoring rule."
