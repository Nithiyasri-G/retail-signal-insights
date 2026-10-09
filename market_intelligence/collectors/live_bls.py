from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple
import pandas as pd

from market_intelligence.config.settings import BLS_CPI_SERIES

from market_intelligence.infra import http as http_client

def build_cpi_signal(data: Dict[str, Any], label: str, series_id: str, retailer: str) -> Tuple[Optional[Dict[str, Any]], pd.DataFrame]:
    series = data.get("Results", {}).get("series", [])
    rows = []
    for item in (series[0].get("data", []) if series else [])[:24]:
        period = item.get("period", "")
        if period == "M13":
            continue
        try:
            cpi_value = float(item["value"])
        except (TypeError, ValueError, KeyError):
            continue
        rows.append(
            {
                "category": label,
                "series_id": series_id,
                "year": int(item["year"]),
                "period": period,
                "month": item.get("periodName", ""),
                "cpi_value": cpi_value,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return None, df
    df = df.sort_values(["year", "period"]).reset_index(drop=True)
    df["cpi_mom_change_pct"] = float("nan")
    # Join by calendar year-month rather than row position. BLS can omit a month
    # (for example a provisional October), and pct_change(12) would then compare
    # August 2026 with July 2025 while still labeling it YoY.
    df["year_month"] = pd.to_datetime(
        df["year"].astype(str) + "-" + df["period"].str.replace("M", "", regex=False).str.zfill(2) + "-01",
        errors="coerce",
    )
    values_by_month = df.set_index("year_month")["cpi_value"]
    previous_month = (df["year_month"] - pd.DateOffset(months=1)).map(values_by_month)
    df["cpi_mom_change_pct"] = (df["cpi_value"] / previous_month - 1) * 100
    prior = df[["year_month", "cpi_value"]].rename(
        columns={"year_month": "prior_year_month", "cpi_value": "prior_cpi_value"}
    )
    df = df.merge(
        prior,
        left_on=df["year_month"] - pd.DateOffset(years=1),
        right_on="prior_year_month",
        how="left",
    ).drop(columns=["key_0", "prior_year_month"], errors="ignore")
    df["cpi_yoy_change_pct"] = (df["cpi_value"] / df["prior_cpi_value"] - 1.0) * 100
    latest = df.iloc[-1].to_dict()
    mom = latest.get("cpi_mom_change_pct")
    yoy = latest.get("cpi_yoy_change_pct")
    score = 4.0
    if pd.notna(mom):
        score = min(10.0, max(1.0, 4.0 + float(mom) * 5.0))
    if pd.notna(yoy) and yoy > 4:
        score = min(10.0, score + 1.0)
    mom_label = "unavailable" if pd.isna(mom) else f"{float(mom):.2f}% MoM"
    yoy_label = "unavailable" if pd.isna(yoy) else f"{float(yoy):.2f}% YoY"
    signal_name = "inflation_pressure_score" if label == "Headline CPI" else f"{label.lower().replace(' ', '_')}_cpi_pressure_score"
    signal = {
        "date": f"{int(latest['year'])}-{str(latest['period']).replace('M', '').zfill(2)}",
        "retailer": retailer,
        "region": "US",
        "region_scope": "national",
        "source": "BLS CPI",
        "signal_area": "Inflation" if label == "Headline CPI" else "Category CPI",
        "signal_name": signal_name,
        "signal_value": round(float(score), 2),
        "risk_score": round(float(score), 2),
        "confidence": "High",
        "score_reason": (
            f"{label} index is {latest['cpi_value']}, {mom_label} and {yoy_label}. "
            "The level tracks the size and direction of that movement: rising prices raise it, "
            "easing prices lower it."
        ),
        "business_impact": f"{label} inflation can affect price sensitivity, category demand, and basket mix.",
        "recommended_action": "Use category CPI as an external regressor and validate against internal category sales.",
        "raw_reference": f"{label}: CPI {latest['cpi_value']}",
    }
    return signal, df


def collect_bls_cpi(bls_key: str = "", retailer: str = "Retailer") -> Dict[str, Any]:
    payload: Dict[str, Any] = {"seriesid": list(BLS_CPI_SERIES.values())}
    if bls_key:
        payload["registrationkey"] = bls_key
    signals = []
    tables = []
    raw = {}
    errors = []
    ok, data, msg = http_client.safe_request(
        "https://api.bls.gov/publicAPI/v2/timeseries/data/",
        method="POST",
        headers={"Content-Type": "application/json"},
        json_body=payload,
        timeout=30,
    )
    if not ok:
        return {"status": "failed", "source": "BLS CPI", "error": msg, "raw": None, "rows": []}
    if data.get("status") != "REQUEST_SUCCEEDED":
        return {
            "status": "failed",
            "source": "BLS CPI",
            "error": "; ".join(data.get("message", [])) or "BLS request was not processed.",
            "raw": data,
            "rows": [],
        }
    series_by_id = {
        series.get("seriesID"): series
        for series in data.get("Results", {}).get("series", [])
    }
    for label, series_id in BLS_CPI_SERIES.items():
        series_payload = {"Results": {"series": [series_by_id.get(series_id, {})]}}
        raw[label] = series_payload
        signal, df = build_cpi_signal(series_payload, label, series_id, retailer)
        if signal:
            signals.append(signal)
        if not df.empty:
            tables.append(df)
    if not signals:
        return {"status": "failed", "source": "BLS CPI", "error": "; ".join(errors) or "No CPI rows returned.", "raw": raw, "rows": []}
    table = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
    return {"status": "success", "source": "BLS CPI", "error": "; ".join(errors), "raw": raw, "rows": signals, "table": table}
