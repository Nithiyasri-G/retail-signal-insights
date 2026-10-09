from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Sequence
import pandas as pd
import re


def parse_signal_search_keywords(value: str) -> List[str]:
    return [part.strip() for part in re.split(r"[,\\n]+", value or "") if part.strip()]


def search_interest_summary(items: List[Dict[str, Any]]) -> pd.DataFrame:
    """Show regional findings without presenting a Trends index as a risk score."""
    rows = []
    for item in items:
        keyword = str(item.get("keyword") or "").strip()
        region = str(item.get("region") or "").strip()
        if keyword and region:
            rows.append({"Search term": keyword, "Region with search interest": region})
    return pd.DataFrame(rows).drop_duplicates().head(20) if rows else pd.DataFrame(
        columns=["Search term", "Region with search interest"]
    )


def related_search_summary(raw_items: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for item in raw_items:
        term = str(item.get("searchTerm") or "").strip()
        for field, label in (("relatedQueries_rising", "Rising"), ("relatedQueries_top", "Related")):
            for query in (item.get(field) or [])[:5]:
                name = str(query.get("query") or "").strip() if isinstance(query, dict) else ""
                if term and name:
                    rows.append({"Search term": term, "Related search": name, "Type": label})
    return pd.DataFrame(rows).drop_duplicates() if rows else pd.DataFrame(
        columns=["Search term", "Related search", "Type"]
    )


US_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def build_default_news_keywords(retailer_name: str, states: Optional[Sequence[str]] = None) -> str:
    """Default GNews search terms: the company, the supply chain and wider market, and local weather.

    The company name is quoted so Google matches the exact phrase instead of stray words.
    ``states`` are the two-letter codes the store network sits in (the NOAA weather areas);
    each one adds two weather searches so local storms and flooding reach the news signal too.
    """
    name = retailer_name.strip() or "Retailer"
    company = f'"{name}"'
    lines = [
        # The company itself: what planners and buyers act on.
        f"{company} recall",
        f"{company} tariffs",
        f"{company} price increase",
        f"{company} store closures",
        f"{company} distribution center",
        # Supply chain and the wider market (geopolitics, shipping, freight, consumers).
        "retail import tariffs",
        "Red Sea shipping disruption",
        "Middle East conflict oil prices supply chain",
        "port strike shipping delays",
        "freight rates trucking retail",
        "consumer spending discount retailers",
        "grocery food inflation",
    ]
    # Weather around the stores, from the states the network operates in.
    for code in states or []:
        state_name = US_STATE_NAMES.get(str(code).strip().upper())
        if state_name:
            lines.append(f"{state_name} severe weather storms")
            lines.append(f"{state_name} flooding hurricane")
    return "\n".join(lines)

def build_default_trends_keywords(retailer_name: str) -> str:
    name = retailer_name.strip() or "Retailer"
    return "\n".join(
        [
            f"{name} sales",
            f"{name} coupons",
            f"{name} near me",
            f"{name} groceries",
            "cheap groceries",
        ]
    )


def retailer_initials(retailer_name: str) -> str:
    words = [word for word in re.split(r"\s+", retailer_name.strip()) if word]
    if not words:
        return "AI"
    return "".join(word[0].upper() for word in words[:2])
