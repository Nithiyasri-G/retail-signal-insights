from market_intelligence.data.distribution import distribution_states as parse_distribution_states
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
import pandas as pd

from market_intelligence.data.demo import _demo_seed, demo_dc_network, demo_recall_catalog, demo_recall_customer_purchases, demo_recall_dc_inventory, demo_recall_lot_master, demo_recall_sales_summary, demo_recall_store_inventory, demo_recall_substitute_products, demo_recall_supplier_master, demo_store_master


def lot_share_of_quantity(quantity: float, lot_id: str, all_lots_for_product: List[str]) -> float:
    """Deterministically split a product-level quantity across its lots.

    Simplification for the MVP: rather than maintaining a fully separate
    lot-level inventory table, on-hand/in-transit quantities are apportioned
    across a product's known lots using a fixed, deterministic weighting so
    recall analysis can still be reported "at product + lot + location level".
    """
    if not all_lots_for_product:
        return quantity
    weights = [55 + (_demo_seed(lot) % 20) for lot in all_lots_for_product]
    total_weight = sum(weights)
    idx = all_lots_for_product.index(lot_id) if lot_id in all_lots_for_product else 0
    return round(quantity * (weights[idx] / total_weight), 1)


def allocate_lot_units(quantity: float, all_lots_for_product: List[str]) -> Dict[str, int]:
    """Split a product-level quantity across its lots in whole units that add back to the total.

    Same deterministic weighting as ``lot_share_of_quantity``, but physical stock cannot be
    70.5 units: each lot gets the whole-unit floor of its share and the units left over go to the
    lots with the largest fractional remainder (ties broken by lot order). The lot figures
    therefore always sum to the product-level quantity rounded to a whole unit.
    """
    total_units = max(int(round(quantity)), 0)
    if not all_lots_for_product:
        return {}
    weights = [55 + (_demo_seed(lot) % 20) for lot in all_lots_for_product]
    total_weight = sum(weights)
    exact = [total_units * weight / total_weight for weight in weights]
    base = [int(value) for value in exact]
    leftover = total_units - sum(base)
    by_remainder = sorted(range(len(exact)), key=lambda i: (-(exact[i] - base[i]), i))
    for index in by_remainder[:leftover]:
        base[index] += 1
    return dict(zip(all_lots_for_product, base))


RECALL_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Food / Consumables": (
        "food", "snack", "cookie", "candy", "popcorn", "cake", "cereal", "beverage",
        "milk", "peanut", "allergen", "salmonella", "listeria", "soup", "granola",
    ),
    "Personal Care": (
        "first aid", "medical", "drug", "health", "cosmetic", "lotion", "toothpaste",
        "mouthwash", "hygiene", "skin", "bandage",
    ),
    "Household": (
        "detergent", "cleaner", "paper towel", "towel", "soap", "household", "label",
        "misbrand", "mislabel", "yellow color",
    ),
    "Emergency Essentials": (
        "battery", "flashlight", "heater", "water", "salt", "emergency",
    ),
}


def _recall_text(recall_item: Dict[str, Any]) -> str:
    return " ".join(
        str(recall_item.get(key, "") or "")
        for key in (
            "product", "product_description", "reason", "reason_for_recall", "risk_type",
            "recalling_firm", "distribution_pattern", "classification", "code_info",
        )
    ).lower()


def recall_category_from_text(recall_item: Dict[str, Any]) -> str:
    text = _recall_text(recall_item)
    for category, tokens in RECALL_CATEGORY_KEYWORDS.items():
        if any(token in text for token in tokens):
            return category
    return ""


def _store_distribution_overlap(recall_item: Dict[str, Any], store_master: pd.DataFrame) -> tuple[bool, list[str]]:
    states, known = parse_distribution_states(str(recall_item.get("distribution_pattern") or ""))
    present = set(store_master.get("State", pd.Series(dtype=str)).astype(str).str.upper())
    matched = sorted(present & states) if known else []
    return bool(matched), matched


def _candidate_product_estimate(
    recall_item: Dict[str, Any],
    candidate_upc: str,
    store_master: pd.DataFrame,
    store_inventory: pd.DataFrame,
    dc_inventory: pd.DataFrame,
) -> Dict[str, Any]:
    """Stage 2 for a probable product match: which footprint stores carry the candidate, and how much.

    Product-level stock only -- no lot split, because a probable match has no confirmed lot. The
    figures are planning visibility, never confirmed exposure, and nothing here triggers withdrawal.
    """
    states, known = parse_distribution_states(str(recall_item.get("distribution_pattern") or ""))
    empty = {
        "candidate_estimate_available": False,
        "candidate_store_ids": [],
        "candidate_stores_carrying": 0,
        "estimated_store_on_hand": 0,
        "estimated_dc_on_hand": 0,
        "estimated_dc_in_transit": 0,
    }
    if not known:
        return empty
    in_footprint = store_master[store_master["State"].astype(str).str.upper().isin(states)]
    stock = store_inventory[
        (store_inventory["UPC"] == candidate_upc)
        & store_inventory["Store ID"].isin(in_footprint["Store ID"])
        & (pd.to_numeric(store_inventory["On Hand"], errors="coerce").fillna(0) > 0)
    ]
    dc_ids = set(demo_dc_network().loc[lambda d: d["State"].astype(str).str.upper().isin(states), "DC ID"])
    dc_stock = dc_inventory[(dc_inventory["UPC"] == candidate_upc) & dc_inventory["DC ID"].isin(dc_ids)]
    return {
        "candidate_estimate_available": True,
        "candidate_store_ids": sorted(stock["Store ID"].astype(str).unique().tolist()),
        "candidate_stores_carrying": int(stock["Store ID"].nunique()),
        "estimated_store_on_hand": int(round(float(pd.to_numeric(stock["On Hand"], errors="coerce").fillna(0).sum()))),
        "estimated_dc_on_hand": int(round(float(pd.to_numeric(dc_stock["On Hand"], errors="coerce").fillna(0).sum()))),
        "estimated_dc_in_transit": int(round(float(pd.to_numeric(dc_stock["In Transit"], errors="coerce").fillna(0).sum()))),
    }


def _word_stem(token: str) -> str:
    return token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token


def _stems(text: str) -> list:
    return [_word_stem(t) for t in re.findall(r"[a-z0-9]+", str(text).lower())]


def product_title(text: str) -> str:
    """The product-title portion of a recall's product text: everything before the ingredient list.

    openFDA product text is a title followed by "Ingredients: ..." (or "NFI: ..."). Ingredient words are
    not the product, so they are never used to identify it.
    """
    return re.split(r"\b(?:ingredients?|nfi)\b\s*[:\-]?", str(text or ""), maxsplit=1, flags=re.IGNORECASE)[0].strip()


def _name_in_text(name: str, text_stems: list) -> bool:
    """The product name appears as one contiguous run of whole words: no unrelated word in between."""
    name_stems = _stems(name)
    if not name_stems:
        return False
    pattern = r"(?:^| )" + " ".join(map(re.escape, name_stems)) + r"(?: |$)"
    return re.search(pattern, " ".join(text_stems)) is not None


_UNIT_ALIASES = {
    "oz": r"(?:oz|ounces?)", "lb": r"(?:lbs?|pounds?)", "gal": r"(?:gal|gallons?)", "ct": r"(?:ct|count|pack)",
}


def package_size_in_text(size: str, text: str) -> bool:
    """True when the catalog package size (e.g. "12 oz") appears in the recall text. Supporting evidence only."""
    parsed = re.match(r"\s*(\d+(?:\.\d+)?)\s*([a-z]+)", str(size or "").lower())
    if not parsed or parsed.group(2) not in _UNIT_ALIASES:
        return False
    number = re.escape(re.sub(r"\.0+$", "", parsed.group(1)))
    return re.search(rf"(?<![\d.]){number}(?:\.0+)?\s*{_UNIT_ALIASES[parsed.group(2)]}\b", str(text or "").lower()) is not None


def _best_product_text_match(product_text: str, catalog: pd.DataFrame) -> Optional[pd.Series]:
    """The catalog item whose name appears, as a contiguous run of whole words, in the recall's product title.

    Only the title (before "Ingredients") is searched. Whole words only (so "bread" is not found inside
    "shortbread"), singular and plural treated alike. A longer name is more specific and wins ("Bread Pudding"
    over "Bread"); a tie keeps catalog order. Brand, vendor and package size are checked afterwards as
    supporting validation and never create a match on their own.
    """
    title_stems = _stems(product_title(product_text))
    if not title_stems:
        return None
    best, best_score = None, 0
    for _, row in catalog.iterrows():
        score = len(_stems(row["Product"]))
        if score > best_score and _name_in_text(row["Product"], title_stems):
            best, best_score = row, score
    return best


def candidate_store_statement(match: Dict[str, Any]) -> str:
    """The one sentence a planner reads about a probable match: candidates identified, none confirmed."""
    if not match.get("candidate_estimate_available"):
        return "Distribution is not stated, so no stores can be linked to the possible product match; 0 are confirmed affected because the recall UPC and lot have not matched."
    count = int(match.get("candidate_stores_carrying", 0) or 0)
    product = str(match.get("candidate_product", "") or "").strip() or "product"
    verb = "store carries" if count == 1 else "stores carry"
    return f"{count} {verb} the possible {product} match; 0 are confirmed affected because the recall UPC and lot have not matched."


def _brand_or_vendor_in_recall(recall_item: Dict[str, Any], brand: str, vendor: str) -> bool:
    """True when the item's brand or vendor name appears, as whole words, in the recall text or firm."""
    haystack = " ".join([str(recall_item.get("recalling_firm", "") or ""), str(recall_item.get("product", "") or "")]).lower()
    for name in (brand, vendor):
        words = re.findall(r"[a-z0-9]+", str(name or "").lower())
        if words and re.search(r"\b" + r"\W+".join(map(re.escape, words)) + r"\b", haystack):
            return True
    return False


def _empty_recall_exposure_result(
    recall_item: Dict[str, Any],
    *,
    match_status: str,
    match_type: str,
    reason: str,
    category: str = "",
    distribution_states: Optional[list[str]] = None,
) -> Dict[str, Any]:
    return {
        "match_status": match_status,
        "match_type": match_type,
        "match_reason": reason,
        "matched_product": f"Category review: {category}" if category else "Distribution review only",
        "matched_category": category,
        "distribution_states": distribution_states or [],
        "upc": "",
        "matched_lots": [],
        "store_lines": [],
        "total_store_exposure": 0.0,
        "dc_lines": [],
        "total_dc_on_hand": 0.0,
        "total_dc_in_transit": 0.0,
        "units_sold": 0,
        "loyalty_units": 0,
        "anonymous_units": 0,
        "loyalty_customers": 0,
        "unit_price": 0.0,
        "exposed_units": 0.0,
        "financial_exposure": 0.0,
        "financial_exposure_derivation": "Not calculated because no exact UPC/lot exposure is confirmed.",
        "substitute_product": None,
        "substitute_readiness": [],
        "supplier_name": "Not confirmed",
        "supplier_prior_recalls": 0,
        "supplier_review_required": False,
        "recall_item": recall_item,
    }


def _match_recall_core(
    recall_item: Dict[str, Any],
    catalog: Optional[pd.DataFrame] = None,
    lot_master: Optional[pd.DataFrame] = None,
    store_master: Optional[pd.DataFrame] = None,
    store_inventory: Optional[pd.DataFrame] = None,
    dc_inventory: Optional[pd.DataFrame] = None,
    sales_summary: Optional[pd.DataFrame] = None,
    customer_purchases: Optional[pd.DataFrame] = None,
    substitute_products: Optional[pd.DataFrame] = None,
    supplier_master: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """Match one FDA recall record to the demonstration product catalog and, if matched,
    calculate inventory exposure, units already sold, tokenized customer exposure,
    financial exposure, substitute readiness, and a supplier-review flag.

    Match order: exact UPC + exact lot -> exact UPC with unknown/no lot -> product-text
    match (marked probable, never counted as confirmed exposure) -> unmatched. All internal
    detail here is demonstration data; only the recall record itself is live FDA data.
    """
    catalog = catalog if catalog is not None else demo_recall_catalog()
    lot_master = lot_master if lot_master is not None else demo_recall_lot_master()
    store_master = store_master if store_master is not None else demo_store_master()
    store_inventory = store_inventory if store_inventory is not None else demo_recall_store_inventory()
    dc_inventory = dc_inventory if dc_inventory is not None else demo_recall_dc_inventory()
    sales_summary = sales_summary if sales_summary is not None else demo_recall_sales_summary()
    customer_purchases = customer_purchases if customer_purchases is not None else demo_recall_customer_purchases()
    substitute_products = substitute_products if substitute_products is not None else demo_recall_substitute_products()
    supplier_master = supplier_master if supplier_master is not None else demo_recall_supplier_master()

    upcs = [u.strip() for u in str(recall_item.get("upcs", "")).split(",") if u.strip()]
    lots = [lot.strip() for lot in str(recall_item.get("lots", "")).split(",") if lot.strip()]
    product_text = str(recall_item.get("product", ""))

    matched_product = None
    matched_lot_ids: List[str] = []
    match_status = "unmatched"
    # R01: a recall can list multiple UPCs, and the supplied order is not significant --
    # an exact UPC+lot match on the SECOND UPC must win even if the FIRST UPC in the list
    # only has an unconfirmed lot. The previous code returned immediately on the first
    # UPC with an unconfirmed lot, so it could never reach a later UPC's exact match.
    # `continue` past a non-exact UPC instead, and only fall through to
    # lot_review_required after every UPC in the list has been checked.
    lot_review_candidate = None

    for upc in upcs:
        product_row = catalog[catalog["UPC"] == upc]
        if product_row.empty:
            continue
        candidate_product = product_row.iloc[0]
        product_lots = lot_master[lot_master["UPC"] == upc]
        if lots and not product_lots.empty:
            matching_lots = product_lots[
                product_lots["Lot ID"].apply(
                    lambda lot_id: any(
                        lot.strip().casefold() == str(lot_id).strip().casefold()
                        for lot in lots
                    )
                )
            ]
            if not matching_lots.empty:
                matched_product = candidate_product
                matched_lot_ids = matching_lots["Lot ID"].tolist()
                match_status = "confirmed_exact"
                break
        if lot_review_candidate is None:
            lot_review_candidate = candidate_product

    if match_status != "confirmed_exact" and lot_review_candidate is not None:
        # R02: parse distribution state evidence the same way every other exit path
        # does, rather than defaulting to an empty list -- this branch was previously
        # the only one that silently discarded distribution evidence already present in
        # the recall text.
        _, distribution_states = _store_distribution_overlap(recall_item, store_master)
        return _empty_recall_exposure_result(
            recall_item, match_status="lot_review_required", match_type="UPC matched; lot unconfirmed",
            reason="UPC matched, but the supplied lot did not match exactly or no lot was supplied. Confirm lot coverage before quantifying exposure.",
            category=str(lot_review_candidate.get("Category", "")),
            distribution_states=distribution_states,
        )

    if matched_product is None:
        best_product = _best_product_text_match(product_text, catalog)
        if best_product is not None:
            if True:
                matched_product = best_product
                product_lots = lot_master[lot_master["UPC"] == matched_product["UPC"]]
                matched_lot_ids = product_lots["Lot ID"].tolist() if not product_lots.empty else []
                match_status = "probable_text_match"

    if matched_product is None:
        category_match = recall_category_from_text(recall_item)
        distribution_overlap, distribution_states = _store_distribution_overlap(recall_item, store_master)
        if category_match:
            return _empty_recall_exposure_result(
                recall_item,
                match_status="category_hazard_match",
                match_type="Category/Hazard Match",
                reason=(
                    f"No exact UPC/product match found, but the recall text maps to {category_match}. "
                    "Use this as buyer/compliance review evidence, not confirmed Store exposure."
                ),
                category=category_match,
                distribution_states=distribution_states,
            )
        if distribution_overlap:
            return _empty_recall_exposure_result(
                recall_item,
                match_status="distribution_match",
                match_type="Distribution Match",
                reason=(
                    "No product/category match found, but the recall distribution footprint overlaps the "
                    "configured Store/DC states. Review before dismissing the incident."
                ),
                distribution_states=distribution_states,
            )
        return _empty_recall_exposure_result(
            recall_item,
            match_status="unmatched",
            match_type="No Internal Match",
            reason="No UPC, product, category, or distribution overlap matched the illustrative internal data.",
        )

    if match_status == "probable_text_match":
        # R02: same distribution-evidence parsing as every other exit path -- this was
        # previously the other branch that silently discarded it.
        _, distribution_states = _store_distribution_overlap(recall_item, store_master)
        result = _empty_recall_exposure_result(
            recall_item, match_status="product_review_required", match_type="Probable product text match",
            reason="Product description matches a catalogue candidate; confirm UPC and lot before quantifying exposure.",
            category=str(matched_product.get("Category", "")),
            distribution_states=distribution_states,
        )
        # The candidate is carried for the reviewer, but kept out of "matched_product" and "upc":
        # nothing about this product is confirmed, and no quantity is estimated from it.
        result["candidate_product"] = str(matched_product.get("Product", ""))
        result["candidate_upc"] = str(matched_product.get("UPC", ""))
        vendor_rows = supplier_master[supplier_master["Supplier ID"] == matched_product.get("Supplier ID")]
        vendor_name = str(vendor_rows.iloc[0]["Supplier Name"]) if not vendor_rows.empty else ""
        result["candidate_brand"] = str(matched_product.get("Brand", "") or "")
        result["candidate_vendor"] = vendor_name
        result["candidate_package_size"] = str(matched_product.get("Package Size", "") or "")
        result["brand_matched"] = _brand_or_vendor_in_recall(recall_item, result["candidate_brand"], vendor_name)
        result["size_matched"] = package_size_in_text(result["candidate_package_size"], product_text)
        result["match_reason"] = (
            f"The product title matches catalogue candidate {result['candidate_product']}. Supporting checks: "
            f"brand/vendor {'found' if result['brand_matched'] else 'not found'}, "
            f"package size {'found' if result['size_matched'] else 'not found'}. "
            "Candidate only; confirm UPC and lot before quantifying exposure."
        )
        result.update(_candidate_product_estimate(recall_item, str(matched_product["UPC"]), store_master, store_inventory, dc_inventory))
        return result
    upc = matched_product["UPC"]
    product_name = matched_product["Product"]
    all_product_lots = lot_master.loc[lot_master["UPC"] == upc, "Lot ID"].tolist()
    footprint, known_footprint = parse_distribution_states(str(recall_item.get("distribution_pattern") or ""))
    if not known_footprint:
        return _empty_recall_exposure_result(
            recall_item, match_status="distribution_review_required", match_type="UPC/lot matched; distribution unconfirmed",
            reason="Distribution is missing or cannot be resolved. Firm address is not distribution evidence.",
            category=str(matched_product.get("Category", "")),
        )
    store_master = store_master[store_master["State"].astype(str).str.upper().isin(footprint)]
    allowed_store_ids = set(store_master["Store ID"].astype(str))
    dc_master = demo_dc_network()
    allowed_dc_ids = set(dc_master.loc[dc_master["State"].astype(str).str.upper().isin(footprint), "DC ID"])
    dc_inventory = dc_inventory[dc_inventory["DC ID"].isin(allowed_dc_ids)]

    store_lines = []
    total_store_exposure = 0.0
    for _, store in store_master.iterrows():
        inv_row = store_inventory[(store_inventory["Store ID"] == store["Store ID"]) & (store_inventory["UPC"] == upc)]
        if inv_row.empty:
            continue
        on_hand = float(inv_row.iloc[0]["On Hand"])
        if matched_lot_ids:
            store_allocation = allocate_lot_units(on_hand, all_product_lots)
            for lot_id in matched_lot_ids:
                lot_on_hand = store_allocation.get(lot_id, 0)
                if lot_on_hand <= 0:
                    continue
                store_lines.append({"Store ID": store["Store ID"], "Store Name": store["Store Name"], "Lot ID": lot_id, "On Hand": lot_on_hand})
                total_store_exposure += lot_on_hand
        else:
            store_lines.append({"Store ID": store["Store ID"], "Store Name": store["Store Name"], "Lot ID": "Unknown lot", "On Hand": int(round(on_hand))})
            total_store_exposure += int(round(on_hand))

    dc_lines = []
    total_dc_on_hand = 0.0
    total_dc_in_transit = 0.0
    for _, dc in dc_inventory[dc_inventory["UPC"] == upc].iterrows():
        lot_ids = matched_lot_ids or ["Unknown lot"]
        dc_on_hand_split = allocate_lot_units(float(dc["On Hand"]), all_product_lots)
        dc_in_transit_split = allocate_lot_units(float(dc["In Transit"]), all_product_lots)
        for lot_id in lot_ids:
            if matched_lot_ids:
                on_hand = dc_on_hand_split.get(lot_id, 0)
                in_transit = dc_in_transit_split.get(lot_id, 0)
            else:
                on_hand = int(round(float(dc["On Hand"])))
                in_transit = int(round(float(dc["In Transit"])))
            dc_lines.append({"DC ID": dc["DC ID"], "Lot ID": lot_id, "On Hand": on_hand, "In Transit": in_transit})
            total_dc_on_hand += on_hand
            total_dc_in_transit += in_transit

    sold_rows = sales_summary[(sales_summary["UPC"] == upc) & sales_summary["Store ID"].astype(str).isin(allowed_store_ids)]
    if matched_lot_ids:
        sold_rows = sold_rows[sold_rows["Lot ID"].isin(matched_lot_ids)]
    units_sold = int(sold_rows["Units Sold"].sum()) if not sold_rows.empty else 0

    # R05: customer_purchases.csv has no UPC column, only "Product" -- join on Product
    # too, not just Lot ID + Store ID. Lot IDs are not guaranteed unique across products
    # in general (this fixture's convention of deriving them from the UPC is a
    # coincidence, not an enforced constraint), so a Lot ID collision between two
    # different products would otherwise count a customer exposed to Product B's recall
    # just because they bought Product A at the same store under a colliding lot code.
    cust_rows = (
        customer_purchases[
            customer_purchases["Lot ID"].isin(matched_lot_ids)
            & customer_purchases["Store ID"].astype(str).isin(allowed_store_ids)
            & (customer_purchases["Product"] == product_name)
        ]
        if matched_lot_ids
        else pd.DataFrame()
    )
    loyalty_units = int(cust_rows[cust_rows["Exposure Type"] == "Loyalty-linked"]["Units"].sum()) if not cust_rows.empty else 0
    anonymous_units = int(cust_rows[cust_rows["Exposure Type"] != "Loyalty-linked"]["Units"].sum()) if not cust_rows.empty else 0
    loyalty_customers = int(cust_rows[cust_rows["Exposure Type"] == "Loyalty-linked"]["Customer Token"].nunique()) if not cust_rows.empty else 0

    unit_price = float(matched_product["Unit Price"])
    exposed_units = total_store_exposure + total_dc_on_hand + total_dc_in_transit
    financial_exposure = round(exposed_units * unit_price, 2)

    substitute_row = substitute_products[substitute_products["Product"] == product_name]
    substitute_name = substitute_row.iloc[0]["Substitute Product"] if not substitute_row.empty else None
    substitute_readiness = []
    if substitute_name:
        sub_upc_row = catalog[catalog["Product"] == substitute_name]
        sub_upc = sub_upc_row.iloc[0]["UPC"] if not sub_upc_row.empty else None
        if sub_upc:
            for _, store in store_master.iterrows():
                sub_inv = store_inventory[(store_inventory["Store ID"] == store["Store ID"]) & (store_inventory["UPC"] == sub_upc)]
                if not sub_inv.empty:
                    on_hand = float(sub_inv.iloc[0]["On Hand"])
                    substitute_readiness.append({"Store ID": store["Store ID"], "Substitute On Hand": on_hand, "Sufficient": on_hand > 20})

    supplier_row = supplier_master[supplier_master["Supplier ID"] == matched_product["Supplier ID"]]
    supplier_name = supplier_row.iloc[0]["Supplier Name"] if not supplier_row.empty else "Unknown supplier"
    prior_recalls = int(supplier_row.iloc[0]["Prior Recall Count"]) if not supplier_row.empty else 0

    return {
        "match_status": match_status,
        "match_type": "Exact UPC/Lot Match" if match_status == "confirmed_exact" else "UPC Match" if match_status == "confirmed_upc_only" else "Product/Vendor Match",
        "match_reason": "Exact UPC/lot matched internal item and lot data." if match_status == "confirmed_exact" else "UPC matched internal item data; recall lot was not confirmed." if match_status == "confirmed_upc_only" else "Product text matched an internal catalog item; UPC/lot confirmation is still required.",
        "is_probable": match_status == "probable_text_match",
        "matched_product": product_name,
        "upc": upc,
        "matched_lots": matched_lot_ids,
        "store_lines": store_lines,
        "total_store_exposure": int(round(total_store_exposure)),
        "dc_lines": dc_lines,
        "total_dc_on_hand": int(round(total_dc_on_hand)),
        "total_dc_in_transit": int(round(total_dc_in_transit)),
        "units_sold": units_sold,
        "loyalty_units": loyalty_units,
        "anonymous_units": anonymous_units,
        "loyalty_customers": loyalty_customers,
        "unit_price": unit_price,
        "exposed_units": int(round(exposed_units)),
        "financial_exposure": financial_exposure,
        "financial_exposure_derivation": (
            f"{exposed_units:,.0f} estimated exposed units (store + DC on-hand + in-transit) x ${unit_price:.2f} unit price = ${financial_exposure:,.2f}."
        ),
        "substitute_product": substitute_name,
        "substitute_readiness": substitute_readiness,
        "supplier_name": supplier_name,
        "supplier_prior_recalls": prior_recalls,
        "supplier_review_required": prior_recalls >= 2,
        "recall_item": recall_item,
    }


def match_recall_to_catalog(
    recall_item: Dict[str, Any],
    catalog: Optional[pd.DataFrame] = None,
    lot_master: Optional[pd.DataFrame] = None,
    store_master: Optional[pd.DataFrame] = None,
    store_inventory: Optional[pd.DataFrame] = None,
    dc_inventory: Optional[pd.DataFrame] = None,
    sales_summary: Optional[pd.DataFrame] = None,
    customer_purchases: Optional[pd.DataFrame] = None,
    substitute_products: Optional[pd.DataFrame] = None,
    supplier_master: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """Match a recall to the catalogue (see ``_match_recall_core``) and add the distribution footprint.

    The footprint is the first, widest stage of store mapping: the stores in the states the recall
    was distributed to. It says nothing about whether those stores carry the product, so the stores
    are candidates only -- never "affected" -- until an exact UPC and lot match confirms exposure.
    """
    result = _match_recall_core(
        recall_item, catalog, lot_master, store_master, store_inventory, dc_inventory,
        sales_summary, customer_purchases, substitute_products, supplier_master,
    )
    stores = store_master if store_master is not None else demo_store_master()
    footprint_states, known = parse_distribution_states(str(recall_item.get("distribution_pattern") or ""))
    in_footprint = (
        stores[stores["State"].astype(str).str.upper().isin(footprint_states)] if known else stores.iloc[0:0]
    )
    result["distribution_known"] = bool(known)
    result["footprint_store_ids"] = sorted(in_footprint["Store ID"].astype(str).tolist())
    result["footprint_store_count"] = len(result["footprint_store_ids"])
    return result
