from typing import Any
from typing import Dict
import pandas as pd

from market_intelligence.signals.labels import readable_signal_name


def retail_context_for_signal(row: Dict[str, Any]) -> Dict[str, str]:
    area = str(row.get("signal_area", "")).lower()
    name = str(row.get("signal_name", "")).lower()
    source = str(row.get("source", "")).lower()
    raw_category = str(row.get("affected_category", "") or row.get("raw_reference", "")).lower()
    score = float(row.get("risk_score", 0) or 0)
    priority = "High" if score >= 8 else "Medium" if score >= 5 else "Monitor"

    context = {
        "retail_category": "Total store / value basket",
        "enterprise_kpi": "Forecast Adjustment Priority",
        "planning_owner": "Demand Planning",
        "demand_direction": "Unknown until matched to POS",
        "forecast_feature": str(row.get("signal_name", "external_signal")),
        "retail_relevance": "External context signal; validate against internal sales, inventory, promotion, and store data.",
        "impact_hypothesis": "Use as a candidate external regressor, not as a standalone demand forecast.",
        "action_priority": priority,
        "evidence_grade": "Live public API" if source else "Unknown",
        "internal_data_needed": "POS sales, category hierarchy, inventory, store/DC mapping, promotion calendar",
    }

    # D04: a CPI signal's raw_reference text (e.g. "Food at home: CPI 298.5") contains
    # the bare word "food", which previously satisfied this branch's keyword check before
    # the dedicated "food at home" CPI branch below ever got a chance to run -- branch
    # order, not intent, misclassified every Food-at-Home CPI signal as a recall/safety
    # issue. CPI signals are identified by source/area, never recall/food-safety ones, so
    # excluding them here is safe and lets the correct CPI-specific branch fire instead.
    is_cpi_signal = "cpi" in area or "cpi" in source
    if not is_cpi_signal and (
        "recall" in area or "fda" in source or any(term in raw_category for term in ["snack", "candy", "beverage", "food"])
    ):
        context.update(
            {
                "retail_category": "Snacks, candy, beverages, and consumables",
                "enterprise_kpi": "Safety and Compliance Risk",
                "planning_owner": "Compliance + Category Buyer",
                "demand_direction": "Downside risk / substitution demand",
                "forecast_feature": "recall_exposure_score",
                "retail_relevance": "Retailers may carry high-velocity consumables where recalls can require UPC matching, vendor review, and store withdrawal decisions.",
                "impact_hypothesis": "If affected UPCs overlap internal SKU files, demand may shift away from recalled items and toward substitutes.",
                "internal_data_needed": "SKU master, UPC list, vendor file, on-hand inventory, store distribution",
            }
        )
    elif "weather" in area or "noaa" in source:
        context.update(
            {
                "retail_category": "Emergency demand: batteries, water, cleaning, food, household essentials",
                "enterprise_kpi": "Supply Chain Disruption Risk",
                "planning_owner": "Supply Chain + Demand Planning",
                "demand_direction": "Short-term uplift for essentials; disruption risk for replenishment",
                "forecast_feature": "state_weather_disruption_score",
                "retail_relevance": "Weather alerts can affect store traffic, replenishment routes, staffing, and emergency-demand baskets in exposed states.",
                "impact_hypothesis": "Severe alerts can lift emergency categories while increasing DC-to-store execution risk.",
                "internal_data_needed": "Store locations, DC routes, state/category sales, inventory by store",
            }
        )
    elif "headline cpi" in name or ("inflation" in area and "category" not in area):
        context.update(
            {
                "retail_category": "Total value basket",
                "enterprise_kpi": "Value Basket Pressure",
                "planning_owner": "Merchandising Strategy + Demand Planning",
                "demand_direction": "Trade-down support for essentials; pressure on discretionary baskets",
                "forecast_feature": "headline_cpi_value_pressure",
                "retail_relevance": "Value-oriented retail demand can be sensitive to consumer price pressure and trade-down behavior.",
                "impact_hypothesis": "Higher inflation can increase value-seeking traffic while changing mix toward essentials.",
                "internal_data_needed": "Traffic, basket mix, category sales, price/promotion calendar",
            }
        )
    elif "gasoline" in name or "gasoline" in raw_category:
        context.update(
            {
                "retail_category": "Traffic-sensitive baskets and discretionary add-ons",
                "enterprise_kpi": "Consumer Wallet Pressure",
                "planning_owner": "Demand Planning + Store Operations",
                "demand_direction": "Possible traffic pressure; essential-item substitution risk",
                "forecast_feature": "gasoline_wallet_pressure_score",
                "retail_relevance": "Fuel inflation can reduce discretionary spend and change trip patterns for value retailers.",
                "impact_hypothesis": "Rising gas prices may shift basket composition and store visit frequency by region.",
                "internal_data_needed": "Store traffic, basket value, region/store sales, trip frequency",
            }
        )
    elif "food_at_home" in name:
        # D04: build_cpi_signal() derives signal_name from the label with spaces
        # replaced by underscores ("food_at_home_cpi_pressure_score"), never with a
        # literal space -- the previous "food at home" (with a space) check could never
        # match the signal_name this branch is actually meant to catch, a second,
        # independent bug alongside the branch-order issue above.
        context.update(
            {
                "retail_category": "Food, snacks, candy, beverages",
                "enterprise_kpi": "Consumables Demand Pressure",
                "planning_owner": "Consumables Category Manager",
                "demand_direction": "Potential uplift in value consumables",
                "forecast_feature": "food_at_home_cpi_pressure",
                "retail_relevance": "Food inflation can push shoppers toward value-format consumables and smaller pack sizes.",
                "impact_hypothesis": "Higher food CPI may increase demand for value snacks, pantry, and beverage alternatives.",
                "internal_data_needed": "Consumables sales, price ladder, pack size, inventory and promotions",
            }
        )
    elif "household" in name:
        context.update(
            {
                "retail_category": "Household supplies, cleaning, home basics",
                "enterprise_kpi": "Household Essentials Pressure",
                "planning_owner": "Household Category Manager",
                "demand_direction": "Potential mix shift toward value household items",
                "forecast_feature": "household_cpi_pressure",
                "retail_relevance": "Household inflation can influence trade-down into value-oriented home and cleaning categories.",
                "impact_hypothesis": "Higher household CPI may support value demand but pressure margin and vendor costs.",
                "internal_data_needed": "Household category sales, costs, vendor data, pricing actions",
            }
        )
    elif "search" in area or "apify" in source:
        context.update(
            {
                "retail_category": "Seasonal, local demand, and store-intent categories",
                "enterprise_kpi": "Demand Interest Spike",
                "planning_owner": "Demand Planning + Digital/Marketing",
                "demand_direction": "Potential regional demand uplift",
                "forecast_feature": "google_trends_interest_score",
                "retail_relevance": "Search interest can reveal early regional intent around deals, coupons, seasonal products, or store visits.",
                "impact_hypothesis": "Rising search interest may precede demand spikes, but must be checked against store/category sales.",
                "internal_data_needed": "Regional sales, promotion calendar, store traffic, search keywords by category",
            }
        )
    elif "news" in area or "gnews" in source:
        context.update(
            {
                "retail_category": "Enterprise market events and competitor pressure",
                "enterprise_kpi": "Market Event Risk",
                "planning_owner": "Merchandising Leadership + Category Manager",
                "demand_direction": "Depends on event type and affected category",
                "forecast_feature": "retail_news_event_score",
                "retail_relevance": "Retail news can surface competitor moves, price pressure, closures, recalls, and market events relevant to retail planning.",
                "impact_hypothesis": "Use high-risk articles as explainers for forecast variance and buyer review.",
                "internal_data_needed": "Affected category sales, competitor set, promotion calendar, store overlap",
            }
        )
    return context


def enrich_feature_rows_for_retailer(feature_df: pd.DataFrame, retailer_name: str) -> pd.DataFrame:
    if feature_df.empty:
        return feature_df
    enriched = feature_df.copy()
    units = {"Weather Risk": "alert_count", "Product Recalls": "recall_record_count", "Retail News": "article_count", "Search Demand": "relative_regional_interest_index_0_100", "Inflation": "engineered_pressure_score_0_10", "Category CPI": "engineered_pressure_score_0_10"}
    enriched["signal_value_unit"] = enriched["signal_area"].map(units).fillna("source_specific")
    enriched["feature_contract_version"] = "2.0"
    enriched["forecast_validation_status"] = "Candidate only; not validated against internal sales"
    contexts = [retail_context_for_signal(row.to_dict()) for _, row in enriched.iterrows()]
    context_df = pd.DataFrame(contexts)
    for col in context_df.columns:
        enriched[col] = context_df[col].values
    # Relevance wording is intentionally retailer-neutral so the application can be reused across companies.
    return enriched


def source_confidence(publisher: str) -> str:
    high = ["reuters", "associated press", "ap news", "bloomberg", "sec", "pr newswire"]
    medium = ["cnbc", "yahoo", "nasdaq", "marketwatch", "forbes", "investing.com", "retail dive"]
    p = (publisher or "").lower()
    if any(name in p for name in high):
        return "High"
    if any(name in p for name in medium):
        return "Medium"
    return "Medium" if publisher else "Low"


def signal_display_label(row: pd.Series, index: int = 0) -> str:
    """Chart/list label: region and signal name only (``index`` is kept for callers, unused)."""
    area = str(row.get("signal_area") or "Signal")
    region = str(row.get("region") or "US")
    name = readable_signal_name(str(row.get("signal_name") or area))
    return f"{region} \u00b7 {name}"


def signal_short_label(row: pd.Series) -> str:
    """Compact name for headlines: 'GA Weather Risk', 'Product Recalls', 'Gasoline CPI'."""
    area = str(row.get("signal_area") or "Signal")
    region = str(row.get("region") or "").strip()
    if area == "Category CPI":
        name = readable_signal_name(str(row.get("signal_name") or area))
        for suffix in (" Pressure Score", " Score"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        return name
    if region and region.upper() != "US":
        return f"{region} {area}"
    return area


def top_level_signals(feature_df: pd.DataFrame) -> pd.DataFrame:
    """Every row in the highest visible level (High/Medium/Low), highest score first.

    Rows tied at the top level are all returned, so a headline never picks one of
    several equally-ranked signals just because it came first in the table.
    """
    from market_intelligence.signals.risk import risk_band

    if feature_df is None or feature_df.empty or "risk_score" not in feature_df.columns:
        return pd.DataFrame()
    ordered = feature_df.sort_values("risk_score", ascending=False, kind="mergesort")
    top_band = risk_band(float(ordered.iloc[0]["risk_score"] or 0))
    return ordered[ordered["risk_score"].apply(lambda value: risk_band(float(value or 0))) == top_band]


def top_signal_labels(feature_df: pd.DataFrame) -> tuple:
    """(labels, level) for every signal tied at the top level, for bulleted display."""
    from market_intelligence.signals.risk import risk_band

    top = top_level_signals(feature_df)
    if top.empty:
        return [], ""
    return [signal_short_label(row) for _, row in top.iterrows()], risk_band(float(top.iloc[0]["risk_score"] or 0))


def top_signals_summary(feature_df: pd.DataFrame, limit: int = 3) -> tuple:
    """(text, level): e.g. ('Gasoline CPI, Product Recalls, GA Weather Risk', 'High')."""
    from market_intelligence.signals.risk import risk_band

    top = top_level_signals(feature_df)
    if top.empty:
        return "No signal", ""
    labels = [signal_short_label(row) for _, row in top.iterrows()]
    text = ", ".join(labels[:limit])
    if len(labels) > limit:
        text += f" +{len(labels) - limit} more"
    return text, risk_band(float(top.iloc[0]["risk_score"] or 0))
