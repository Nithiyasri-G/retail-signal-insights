import pandas as pd


def risk_band(score: float) -> str:
    if pd.isna(score):
        return "Not evaluated"
    if score >= 8:
        return "High"
    if score >= 5:
        return "Medium"
    return "Low"
