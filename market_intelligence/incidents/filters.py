from typing import List
from typing import Optional
from typing import Tuple
import pandas as pd


def apply_incident_scope_filters(
    df: pd.DataFrame,
    *,
    store_filter: Optional[List[str]] = None,
    product_filter: Optional[List[str]] = None,
    primary_dc_filter: Optional[List[str]] = None,
    backup_dc_filter: Optional[List[str]] = None,
    dimensions: Tuple[str, ...] = ("store", "product", "primary_dc", "backup_dc"),
) -> pd.DataFrame:
    """Shared filter context for the Weather incident detail view's Store/Product/DC
    filters. Each table declares which dimensions actually apply to IT, rather than one
    universal predicate applied uniformly to every table regardless of its own shape:
    staffing/employee records are per-Store only (there is no Product or DC dimension in
    that data at all), so only the Store filter may narrow them -- a Product or
    Primary/Backup DC filter selection must never silently drop staffing rows that have
    nothing to do with either. `dimensions` is each table's own declared scope, not an
    inferred one, so a table can never be filtered on a dimension it didn't opt into even
    if a same-named column happened to exist.
    """
    filtered = df
    if "store" in dimensions and store_filter and "Store ID" in filtered.columns:
        filtered = filtered[filtered["Store ID"].astype(str).isin(store_filter)]
    if "product" in dimensions and product_filter and "Product" in filtered.columns:
        filtered = filtered[filtered["Product"].astype(str).isin(product_filter)]
    if "primary_dc" in dimensions and primary_dc_filter and "Primary DC" in filtered.columns:
        filtered = filtered[filtered["Primary DC"].astype(str).isin(primary_dc_filter)]
    if "backup_dc" in dimensions and backup_dc_filter and "Backup DC" in filtered.columns:
        filtered = filtered[filtered["Backup DC"].astype(str).isin(backup_dc_filter)]
    return filtered
