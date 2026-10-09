"""The recall evidence ladder: what each level proves, and what a planner may do about it.

One table, used by the incident builder (the action text), the incident detail (the evidence panel)
and the tests, so the wording a planner reads can never differ between screens.
"""
from typing import Dict, Optional

# Ordered strongest to weakest evidence.
EVIDENCE_LADDER: Dict[str, Dict[str, object]] = {
    "confirmed_exact": {
        "level": "Exact UPC and exact lot",
        "confirmed": "Product, UPC and lot all match the internal catalogue.",
        "estimated": "Lot-level quantities are whole-unit allocations of product-level inventory.",
        "required": "Nothing further to identify the product; confirm physical lot labels when isolating stock.",
        "store_action": True,
        "action": "Isolate confirmed affected inventory and initiate withdrawal.",
    },
    "confirmed_upc_only": {
        "level": "Exact UPC, lot confirmation required",
        "confirmed": "The UPC matches an internal item.",
        "estimated": "No lot-level exposure is quantified.",
        "required": "Confirm which lots the recall covers.",
        "store_action": False,
        "action": "Hold withdrawal until lot coverage is confirmed.",
    },
    "lot_review_required": {
        "level": "Exact UPC, lot confirmation required",
        "confirmed": "The UPC matches an internal item.",
        "estimated": "No lot-level exposure is quantified.",
        "required": "Confirm which lots the recall covers.",
        "store_action": False,
        "action": "Hold withdrawal until lot coverage is confirmed.",
    },
    "probable_text_match": {
        "level": "Probable product match",
        "confirmed": "Nothing about the product is confirmed.",
        "estimated": "The product is inferred from the recall description only.",
        "required": "Confirm UPC, brand, package size and vendor.",
        "store_action": False,
        "action": "Validate UPC, brand, package size and vendor before store action.",
    },
    "product_review_required": {
        "level": "Probable product match",
        "confirmed": "Nothing about the product is confirmed.",
        "estimated": "The product is inferred from the recall description only.",
        "required": "Confirm UPC, brand, package size and vendor.",
        "store_action": False,
        "action": "Validate UPC, brand, package size and vendor before store action.",
    },
    "category_hazard_match": {
        "level": "Category or hazard match",
        "confirmed": "The recall text maps to a product category the retailer sells.",
        "estimated": "No product, store or inventory exposure is estimated.",
        "required": "Buyer and compliance review of whether any carried item is affected.",
        "store_action": False,
        "action": "Buyer/compliance review only; no inventory withdrawal.",
    },
    "distribution_match": {
        "level": "Distribution overlap only",
        "confirmed": "The recall's distribution states overlap states where the retailer has stores.",
        "estimated": "No product-level exposure is estimated.",
        "required": "Confirm whether the product is carried internally.",
        "store_action": False,
        "action": "Monitor and confirm whether the product is carried internally.",
    },
    "distribution_review_required": {
        "level": "Distribution overlap only",
        "confirmed": "The product matched, but the distribution could not be resolved.",
        "estimated": "No store exposure is estimated.",
        "required": "Resolve the recall's distribution footprint.",
        "store_action": False,
        "action": "Monitor and confirm whether the product is carried internally.",
    },
    "unmatched": {
        "level": "No internal match",
        "confirmed": "Nothing in the internal data matches this recall.",
        "estimated": "Nothing is estimated.",
        "required": "Nothing currently.",
        "store_action": False,
        "action": "Retain for visibility; no Store action currently applies.",
    },
}


def ladder_entry(match_status: Optional[str]) -> Dict[str, object]:
    return EVIDENCE_LADDER.get(str(match_status or ""), EVIDENCE_LADDER["unmatched"])
