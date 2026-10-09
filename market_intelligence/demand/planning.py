from typing import Any
from typing import Dict
from typing import List
from typing import Tuple
import pandas as pd

from market_intelligence.weather.assumptions import WEATHER_FAMILY_CATEGORY_HINTS, weather_event_family


# The four categories the product catalogue actually uses. A scope must resolve to these
# or it is not a scope -- naming a category the catalogue does not carry would silently
# filter the plan down to nothing.
CATALOG_CATEGORIES: Tuple[str, ...] = (
    "Emergency Essentials",
    "Food / Consumables",
    "Household",
    "Personal Care",
)


CPI_SIGNAL_CATEGORIES: Dict[str, Tuple[str, ...]] = {
    "food_at_home_cpi_pressure_score": ("Food / Consumables",),
    "household_furnishings_cpi_pressure_score": ("Household",),
}


DEMAND_SIGNAL_SCOPE_FIELDS: Tuple[str, str, str] = ("Signal Key", "Top Event", "Catalog Categories")


def signal_store_scope(signal_row: Dict[str, Any], store_master: pd.DataFrame) -> Tuple[Tuple[str, ...], str]:
    """Which stores does this signal actually reach?

    A weather signal is raised for one state, so evaluating it against a store in another
    state answers nothing -- and that mismatch is invisible if the store picker simply
    lists every store. National signals (CPI, retail news) reach all of them. Returns
    (store_ids, basis); an empty tuple means every store is in scope.
    """
    region = str(signal_row.get("Region", "") or "").strip().upper()
    if not region or region == "US" or "State" not in store_master.columns:
        return (), "This signal is national, so every store is in scope."

    in_region = store_master[store_master["State"].astype(str).str.upper() == region]
    if in_region.empty:
        return (), f"No store sits in {region}, so every store is shown as baseline context."
    return (
        tuple(str(value) for value in in_region["Store ID"]),
        f"This signal was raised for {region}, so only stores in {region} are candidate planning context; actual alert intersection is established in the Operational Impact Center.",
    )


def signal_category_scope(signal_row: Dict[str, Any]) -> Tuple[Tuple[str, ...], str]:
    """Which product categories does this signal actually implicate, and on what basis?

    Returns (categories, basis). An empty tuple means the signal cannot be mapped to a
    product category with the data available, in which case the caller evaluates the
    whole store and says so. Guessing a category would be worse than admitting the
    mapping is missing -- that admission is the thing internal product hierarchy would
    fix, and it is what the planner needs to be told.
    """
    area = str(signal_row.get("Signal Area", "") or "").lower()
    signal_key = str(signal_row.get("Signal Key", "") or "").lower()

    if "weather" in area:
        event = str(signal_row.get("Top Event", "") or "").strip()
        family = weather_event_family(event) if event else ""
        hints = tuple(WEATHER_FAMILY_CATEGORY_HINTS.get(family, ()))
        if hints:
            return hints, f"{event} falls in the {family} family, which moves these categories."
        return (), "The alert type is not mapped to a demand category yet."

    if "recall" in area:
        raw_categories = signal_row.get("Catalog Categories")
        if isinstance(raw_categories, str):
            raw_categories = [part.strip() for part in raw_categories.split("|")]
        declared = [
            category
            for category in (raw_categories if raw_categories is not None and not isinstance(raw_categories, float) else [])
            if category in CATALOG_CATEGORIES
        ]
        if declared:
            return tuple(declared), "Recall text maps to these categories in the product catalogue."
        return (), "No recalled product matched a catalogue category."

    if "cpi" in area or "inflation" in area:
        mapped = CPI_SIGNAL_CATEGORIES.get(signal_key)
        if mapped:
            return mapped, "This CPI series tracks these categories directly."
        # Headline CPI and gasoline move the basket and trip frequency, not one shelf.
        return (), "Headline and fuel inflation affect the whole basket, not one category."

    return (), "This signal has no product-level mapping until internal hierarchy is connected."


# Business wording shown to planners; the technical key stays available in Admin view.
FORECAST_INPUT_LABELS: Dict[str, str] = {
    "gasoline_wallet_pressure_score": "Gasoline price pressure",
    "headline_cpi_value_pressure": "Headline inflation pressure",
    "food_at_home_cpi_pressure": "Food-at-home inflation pressure",
    "household_cpi_pressure": "Household inflation pressure",
    "state_weather_disruption_score": "Weather disruption pressure",
    "recall_exposure_score": "Product recall exposure",
    "category_cpi_pressure": "Category inflation pressure",
    "retail_news_event_score": "Retail news event signal",
}


def forecast_input_label(key: Any) -> str:
    text = str(key or "").strip()
    if not text or text.lower() == "nan":
        return ""
    return FORECAST_INPUT_LABELS.get(text, text.replace("_", " ").capitalize())


GAP_DEFINITION = (
    "Current Baseline Supply Gap is the sum of individual product shortages. "
    "Surplus in one product does not offset shortage in another product. "
    "This is the store's existing baseline supply position and is not additional demand caused by the selected signal."
)
SURPLUS_DEFINITION = (
    "Some products have a combined {surplus}-unit surplus relative to their individual baseline forecasts. "
    "This surplus is shown separately and does not offset shortages in other products."
)
AVAILABLE_SUPPLY_ASSUMPTION = (
    "Available Supply currently equals On Hand plus Inbound. Committed inventory and Safety Stock are zero in the "
    "illustrative dataset. All displayed inbound is assumed to arrive within the planning horizon."
)
HISTORICAL_REFERENCE_NOTE = (
    "Historical Sales Reference is shown for comparison only. It does not change the current baseline supply-gap calculation."
)
NO_NEWS_MAPPING_NOTE = (
    "No product/category/store mapping is available for this news event. Detailed supply-gap analysis has not been calculated."
)

# Plan keys are kept stable for callers; these are the labels a planner reads.
PLAN_DISPLAY_KEYS: Dict[str, str] = {
    "Baseline Gap": "Current Baseline Supply Gap",
    "Baseline Surplus": "Product-Level Surplus",
    "Demand Direction": "Planning Review Direction",
    "Forecast Feature": "Potential Forecast Input",
    "Historical Average": "Historical Sales Reference",
}


def display_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {PLAN_DISPLAY_KEYS.get(key, key): value for key, value in plan.items()}


def planning_mode(signal_row: Dict[str, Any]) -> Tuple[str, str]:
    """("full", "") when the signal maps to product categories; otherwise ("context", why).

    A signal that cannot be mapped must never fall back to a whole-store calculation: that
    would show a supply gap the signal did not cause.
    """
    categories, basis = signal_category_scope(signal_row)
    if categories:
        return "full", ""
    area = str(signal_row.get("Signal Area", "") or "").lower()
    if "news" in area:
        return "context", NO_NEWS_MAPPING_NOTE
    return "context", f"{basis} Detailed supply-gap analysis has not been calculated."


def classify_planning_direction(signal_row: Dict[str, Any]) -> str:
    """Translate the Market Intelligence signal into a simple planning direction.

    No user-entered uplift/decline percentage is used. The direction is based on
    the signal type and is intended only to guide the demo planning analysis.
    """
    area = str(signal_row.get("Signal Area", "") or "").lower()
    if "weather" in area:
        event = str(signal_row.get("Top Event") or "").lower()
        if not event or event == "nan":
            return "MONITOR / NO ACTIVE EVENT"
        return "CATEGORY REVIEW / DISRUPTION"
    if "recall" in area:
        return "SLUMP / SUBSTITUTION"
    if "cpi" in area or "inflation" in area:
        return "MIX SHIFT / REVIEW"
    return "REVIEW"


_DEMAND_PLANNING_NUMERIC_INPUT_COLUMNS = ("Baseline Forecast", "Historical Average", "On Hand", "Inbound")


def demand_planning_editor_column_kinds(columns: List[str]) -> Dict[str, str]:
    """D02: classify each Demand Planner editor column as either a planner-editable
    numeric input ("numeric_input", gets a min_value=0 floor -- a negative
    forecast/inventory quantity is never valid) or a disabled, non-editable column
    ("disabled"). Only the four fields calculate_demand_plan() actually reads are
    planner inputs; everything else is an identifier (Store ID, Product, Category) or a
    catalog/computed attribute (Case Pack, MOQ, Sellable Unit, Captured At, Committed,
    Safety Stock, Eligible Inbound Before Cutoff, ATP) that was previously silently
    editable, including free-text identifiers, with no validation at all.
    """
    return {
        col: "numeric_input" if col in _DEMAND_PLANNING_NUMERIC_INPUT_COLUMNS else "disabled"
        for col in columns
    }


def _scoped_products(signal_row: Dict[str, Any], portfolio: pd.DataFrame) -> Tuple[pd.DataFrame, bool, Tuple[str, ...], str]:
    """(products in scope, category-scoped?, categories, basis). A mapped category the store does
    not carry yields zero products, never the whole store."""
    categories, scope_basis = signal_category_scope(signal_row)
    working = portfolio
    scoped = False
    if categories and "Category" in portfolio.columns:
        working = portfolio[portfolio["Category"].astype(str).isin(categories)]
        scoped = True
        if working.empty:
            scope_basis = (
                f"{scope_basis} No products in the mapped category are carried at the selected store. "
                "No supply-gap calculation is available for this signal/store combination."
            )
    if not scoped:
        categories = tuple(
            sorted({str(value) for value in portfolio.get("Category", pd.Series(dtype=str)).dropna()})
        )
    return working, scoped, categories, scope_basis


def demand_plan_product_rows(
    signal_row: Dict[str, Any],
    planning_df: pd.DataFrame,
    store_id: str = "",
    store_name: str = "",
    run_timestamp: str = "",
) -> pd.DataFrame:
    """One numeric row per in-scope product, for the product-level export."""
    portfolio = planning_df.copy()
    for col in ("Baseline Forecast", "Historical Average", "On Hand", "Inbound"):
        if col not in portfolio.columns:
            portfolio[col] = 0
        portfolio[col] = pd.to_numeric(portfolio[col], errors="coerce").fillna(0)
    working, _, _, basis = _scoped_products(signal_row, portfolio)
    shortage = (working["Baseline Forecast"] - working["On Hand"] - working["Inbound"])
    return pd.DataFrame(
        {
            "Run Timestamp": run_timestamp,
            "Signal ID": signal_row.get("Signal ID", ""),
            "Signal Name": signal_row.get("Signal Area", ""),
            "Selected Store ID": store_id,
            "Selected Store": store_name,
            "Product": working.get("Product", ""),
            "Category": working.get("Category", ""),
            "Baseline Forecast": working["Baseline Forecast"].round().astype(int),
            "Historical Sales Reference": working["Historical Average"].round().astype(int),
            "On Hand": working["On Hand"].round().astype(int),
            "Eligible Inbound": working["Inbound"].round().astype(int),
            "Available Supply": (working["On Hand"] + working["Inbound"]).round().astype(int),
            "Product-Level Baseline Shortage": shortage.clip(lower=0).round().astype(int),
            "Product-Level Surplus": (-shortage).clip(lower=0).round().astype(int),
            "Scope Reason": basis,
        }
    ).reset_index(drop=True)


def calculate_demand_plan(
    signal_row: Dict[str, Any],
    planning_df: pd.DataFrame,
    store_name: str = "",
) -> Dict[str, Any]:
    """Evaluate the selected signal against the products that signal actually implicates.

    This used to total every product in the store regardless of the signal, so a recall
    and a heat wave produced the identical figure and the tab read as a smaller copy of
    the Operational Impact Center. The scope now comes from the signal: a Flood Watch
    evaluates emergency, consumable and household lines; a recall evaluates the
    categories its text maps to; a category CPI series evaluates the categories it
    tracks. Where no mapping exists the whole store is evaluated and the plan says so,
    because an honest "not mapped yet" is more useful to a planner than a guess.

    This answers a different question from the Operational Impact Center. That view sizes
    one event's operational response inside its alert window; this one asks whether the
    current baseline forecast already anticipates the signal at all.
    """
    if planning_df is None or planning_df.empty:
        raise ValueError("Reference planning data is required for demand impact analysis.")

    numeric_cols = ["Baseline Forecast", "Historical Average", "On Hand", "Inbound"]
    portfolio = planning_df.copy()
    for col in numeric_cols:
        if col not in portfolio.columns:
            portfolio[col] = 0
        portfolio[col] = pd.to_numeric(portfolio[col], errors="coerce").fillna(0)

    working, scoped, categories, scope_basis = _scoped_products(signal_row, portfolio)

    baseline = float(working["Baseline Forecast"].sum())
    historical = float(working["Historical Average"].sum())
    on_hand = float(working["On Hand"].sum())
    inbound = float(working["Inbound"].sum())
    available_supply = on_hand + inbound
    per_product = working["Baseline Forecast"] - working["On Hand"] - working["Inbound"]
    gap = float(per_product.clip(lower=0).sum())
    surplus = float((-per_product).clip(lower=0).sum())
    direction = classify_planning_direction(signal_row)

    stores = sorted({str(v) for v in portfolio.get("Store ID", pd.Series(dtype=str)).dropna().astype(str).tolist() if str(v).strip()})
    store_id = ", ".join(stores)

    return {
        "Signal ID": signal_row.get("Signal ID", ""),
        "Signal Area": signal_row.get("Signal Area", ""),
        "Signal Level": signal_row.get("Level", ""),
        "Signal Source": signal_row.get("Source", ""),
        "Demand Direction": direction,
        "Forecast Feature": signal_row.get("Forecast Feature", ""),
        "Planning Scope": "Categories this signal implicates" if scoped else "Whole store (signal not category-mapped)",
        "Scope Basis": scope_basis,
        "Selected Store ID": store_id or "Reference store",
        "Selected Store Name": store_name or (f"Store {store_id}" if store_id else "Reference store"),
        "Stores Evaluated": 1,
        "Category Scope": ", ".join(categories) if categories else "Reference categories",
        "Products In Scope": int(len(working)),
        "Products In Store": int(len(portfolio)),
        "Reference Products": int(len(working)),
        "Baseline Forecast": int(round(baseline)),
        "Historical Average": int(round(historical)),
        "On Hand": int(round(on_hand)),
        "Inbound": int(round(inbound)),
        "Available Supply": int(round(available_supply)),
        "Baseline Gap": int(round(gap)),
        "Baseline Surplus": int(round(surplus)),
    }
