from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple
import pandas as pd

from market_intelligence.signals.risk import risk_band
from market_intelligence.util.tables import drop_empty_columns


REGION_SCOPE_LABELS: Dict[str, str] = {
    "national": "National",
    "national_with_state_records": "National, with state-level records",
    "state_weather_alerts": "State weather alerts",
    "country_news": "Country news",
    "trend_geo": "Search geography",
}


def readable_region_scope(value: Any) -> str:
    text = str(value or "").strip()
    return REGION_SCOPE_LABELS.get(text, text.replace("_", " ").capitalize() if text else "")


def readable_signal_name(value: Any) -> str:
    """Turn a model feature id into a readable name without breaking acronyms.

    str.title() turned "cpi" into "Cpi" and "upc" into "Upc", so the cards read
    "Food At Home Cpi Pressure Score".
    """
    acronyms = {"cpi": "CPI", "upc": "UPC", "dc": "DC", "noaa": "NOAA", "fda": "FDA", "atp": "ATP"}
    words = str(value or "").replace("_", " ").split()
    return " ".join(acronyms.get(word.lower(), word.capitalize()) for word in words)


CLIENT_COLUMN_LABELS: Dict[str, str] = {
    # Signal identity
    "source": "Source",
    "signal_area": "Signal Area",
    "signal_name": "Signal Name",
    "signal_value": "Signal Value",
    "region": "Region",
    "region_scope": "Region Scope",
    "confidence": "Confidence",
    "date": "Date",
    "data_period": "Data Period",
    "retailer": "Retailer",
    "selected_market": "Market",
    # Retail framing
    "retail_category": "Retail Category",
    "retail_relevance": "Relevance",
    "enterprise_kpi": "Enterprise KPI",
    "planning_owner": "Planning Owner",
    "demand_direction": "Demand Direction",
    "action_priority": "Action Priority",
    "forecast_feature": "Forecast Feature",
    "impact_hypothesis": "Impact Hypothesis",
    "internal_data_needed": "Internal Data Needed",
    "business_impact": "Business Impact",
    "recommended_action": "Recommended Action",
    "score_reason": "Why This Level",
    "raw_reference": "Evidence Reference",
    "evidence_grade": "Evidence Grade",
    "sku_match_status": "SKU Match Status",
    "movement": "Movement",
    # Validation plan
    "external_signal": "External Signal",
    "validation_analysis": "Validation Analysis",
    "validation_metric": "Validation Metric",
    "decision_use": "Decision Use",
    # Collector evidence
    "status": "Status",
    "endpoint_or_actor": "Endpoint",
    "request_scope": "Request Scope",
    "raw_records_pulled": "Raw Records Pulled",
    "normalized_feature_rows": "Feature Rows",
    "live_request_made": "Live Request Made",
    "used_in_llm_payload": "Used In Brief",
    "source_represented_in_llm_payload": "Used In Brief",
    "feature_rows_in_accepted_payload": "Feature Rows In Brief",
    "article_rows_in_accepted_payload": "Article Rows In Brief",
    "reused_saved_signal": "Reused Saved Signal",
    "mock_data_used": "Mock Data Used",
    "analysis_method": "Analysis Method",
    "error_or_note": "Note",
}


CANDIDATE_VALIDATION_STATUS = "Candidate only \u2014 not validated against internal sales"

FEATURE_TABLE_COLUMNS: Tuple[str, ...] = (
    "date",
    "source",
    "signal_area",
    "signal_name",
    "region",
    "region_scope",
    "retail_category",
    "enterprise_kpi",
    "planning_owner",
    "demand_direction",
    "action_priority",
    "forecast_feature",
    "risk_score",
    "confidence",
    "score_reason",
    "business_impact",
    "recommended_action",
    "internal_data_needed",
    "raw_reference",
)


def readable_feature_values(df: pd.DataFrame) -> pd.DataFrame:
    """Render the model identifiers as names wherever they are shown as values.

    with_client_labels() fixes headers. These columns hold feature ids and scope enums
    as their values -- "supply_chain_weather_risk_score", "state_weather_alerts" --
    which is the same leak one cell to the right.
    """
    if df is None or df.empty:
        return df
    readable = df.copy()
    for column in ("signal_name", "forecast_feature", "Signal Name", "Forecast Feature"):
        if column in readable.columns:
            readable[column] = readable[column].map(readable_signal_name)
    for column in ("region_scope", "Region Scope"):
        if column in readable.columns:
            readable[column] = readable[column].map(readable_region_scope)
    return readable


def _split_run_date_and_data_period(table: pd.DataFrame, run_date: Optional[str]) -> pd.DataFrame:
    """Give every row an unambiguous Run Date plus a Data Period.

    The collectors disagree on what "date" means: news, recalls and weather stamp the run
    day (2026-10-08) but BLS CPI stamps the reporting month it published (2026-08). Mixing
    both in one column reads as inconsistent, so CPI rows keep their reporting month in
    ``Data Period`` ("Aug 2026") and show the run date in ``Run Date``; every other row's
    period is simply "At run time".
    """
    if "date" not in table.columns:
        return table
    out = table.copy()
    month = out["date"].astype(str).str.fullmatch(r"\d{4}-\d{2}")
    periods = pd.Series("At run time", index=out.index)
    periods[month] = pd.to_datetime(out.loc[month, "date"] + "-01", errors="coerce").dt.strftime("%b %Y").fillna(out.loc[month, "date"])
    if run_date:
        out.loc[month, "date"] = run_date
    out.insert(list(out.columns).index("date") + 1, "data_period", periods)
    return out


def client_feature_table(feature_df: pd.DataFrame, *, for_export: bool = False, run_date: Optional[str] = None) -> pd.DataFrame:
    """The signal rows a client should see: a fixed column set, named and readable.

    feature_df is the union of every collector's signal dict, so rendering it whole put
    collector-private keys on screen -- catalog_categories as a Python list,
    is_demo_scenario as a boolean, and a dozen columns blank for every row from a
    different source.
    """
    if feature_df is None or feature_df.empty:
        return pd.DataFrame()
    columns = [column for column in FEATURE_TABLE_COLUMNS if column in feature_df.columns]
    table = _split_run_date_and_data_period(readable_feature_values(feature_df[columns].copy()), run_date)
    if "risk_score" in table.columns:
        table["risk_score"] = table["risk_score"].apply(lambda value: risk_band(float(value or 0)))
        table = table.rename(columns={"risk_score": "Risk Level"})
    table = with_client_labels(table).rename(columns={"Date": "Run Date"})
    if for_export:
        table["Validation Status"] = CANDIDATE_VALIDATION_STATUS
    return table if for_export else table[drop_empty_columns(table, list(table.columns))]


EVIDENCE_CODE_LABELS: Dict[str, str] = {
    "MATCH_ALERT_POLYGON": "Store inside the NOAA alert polygon",
    "MATCH_ALERT_FIPS": "Store county in the NOAA alert's county list",
    "MATCH_COUNTY_NAME_FALLBACK": "Store county matched by name (fallback)",
    "NO_STORE_INTERSECTION": "No store falls inside the alert area",
    "STORE_COVERAGE_MISSING": "Store coverage data not available for this area",
    "EVIDENCE_UNCLASSIFIED": "Not classified",
}


def readable_evidence_code(value: Any) -> str:
    """The geography evidence enum, in words.

    The raw code is the audit key and stays in the exported evidence bundle; on screen
    "MATCH_COUNTY_NAME_FALLBACK" reads as a defect rather than as a weaker match.
    """
    text = str(value or "").strip()
    if not text:
        return "Not classified"
    return EVIDENCE_CODE_LABELS.get(text.upper(), text.replace("_", " ").capitalize())


def with_client_labels(df: pd.DataFrame) -> pd.DataFrame:
    """Give a client-facing table Title Case headers instead of Python identifiers.

    These frames are built from the collector schema, so their columns are the internal
    field names. Rendering them unchanged put `retail_category`, `enterprise_kpi` and
    `score_reason` in front of the client; a name map keeps one source of truth for the
    data and one for the presentation.
    """
    if df is None or df.empty:
        return df
    renames = {column: CLIENT_COLUMN_LABELS[column] for column in df.columns if column in CLIENT_COLUMN_LABELS}
    return df.rename(columns=renames) if renames else df
