"""Product-family matching for recalls: the recall counterpart of the weather event-family ladder.

Weather does not need an exact past event to size an alert. It maps the NOAA event name to an
event family (``WEATHER_EVENT_FAMILY_RULES``), maps the family to the categories it moves
(``WEATHER_FAMILY_CATEGORY_HINTS``), and labels the result Exact -> Analog -> Operational Exposure.

Recalls follow the same shape, customised for products:

* Geography  -- the recall's distribution states select the footprint stores and DCs
  (weather: alert polygon / county FIPS).
* Family     -- keyword rules on the recall's product title map it to a product family
  (weather: event name -> event family).
* Family -> internal assortment -- the SAME keyword rules applied to internal catalogue names give
  the items in that family (weather: family -> category hints). No product list is hard-coded, so
  the rule works for whatever recall arrives, not only today's.
* Evidence ladder -- exact UPC + lot -> UPC only -> product-name match -> product-family (analog)
  match -> category/hazard -> distribution -> none (weather: exact -> analog -> exposure).

A family match is analog evidence. Its quantities are planning estimates for the footprint stores
that carry items of the same family; they are never confirmed affected inventory and never enable
withdrawal or a POS block, which stay reserved for an exact UPC + lot match.
"""
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
import pandas as pd

from market_intelligence.data.distribution import distribution_states as parse_distribution_states


# Ordered: the first family whose keyword appears wins, so more specific families come first
# ("peanut butter" before dairy "butter", "cookie" before "spice", "candy" before "spice").
# Keywords are matched as whole words (singular and plural alike), never inside another word.
PRODUCT_FAMILY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("infant", "baby", "toddler", "formula"), "Infant / Baby"),
    (("cough", "pain reliever", "bandage", "medicine", "antacid", "allergy relief"), "Personal Care / OTC"),
    (("dish soap", "detergent", "cleaner", "dishwasher", "laundry", "trash bag", "foil", "paper towel",
      "napkin", "storage bag", "light bulb"), "Household Supplies"),
    (("tortilla chip", "corn chip", "potato chip"), "Snacks / Chips"),
    (("pet food", "dog food", "cat food", "dog treat", "cat treat", "pet treat"), "Pet Food"),
    (("cookie", "biscuit", "shortbread", "wafer", "brownie", "cake", "cupcake", "muffin", "pastry",
      "donut", "doughnut", "pie", "pudding", "croissant", "danish", "pan dulce", "concha", "baklava"), "Cookies / Sweet Baked Goods"),
    (("candy", "candie", "chocolate", "gummy", "gummie", "lollipop", "toffee", "caramel", "confection",
      "confectionery", "marshmallow", "licorice", "jelly bean", "mint"), "Candy / Confectionery"),
    (("bread", "loaf", "bun", "roll", "bagel", "baguette", "brioche", "pan de", "tortilla", "pita",
      "flatbread", "naan", "bakery"), "Bread / Bakery"),
    (("granola", "cereal", "oat", "oatmeal", "muesli", "porridge"), "Granola / Cereal"),
    (("peanut butter", "almond butter", "nut butter", "jam", "jelly", "preserve", "honey", "syrup"), "Spreads / Sweeteners"),
    (("salsa", "pico de gallo", "guacamole", "hummus", "dip", "queso"), "Salsa / Dips"),
    (("soup", "broth", "bisque", "chowder", "bouillon", "stew", "ramen"), "Soup / Broth"),
    (("sauce", "ketchup", "mustard", "mayonnaise", "dressing", "marinade", "condiment", "relish"), "Sauces / Condiments"),
    (("seasoning", "spice", "dashi", "hondashi", "masala", "curry powder", "rub", "paprika",
      "cumin", "turmeric", "cinnamon", "chili powder"), "Seasoning / Spices"),
    (("color", "coloring", "colour", "dye", "food color", "flavoring", "extract"), "Food Coloring / Additives"),
    (("chip", "crisp", "pretzel", "popcorn", "cracker", "trail mix", "snack", "jerky"), "Snacks / Chips"),
    (("pasta", "spaghetti", "macaroni", "noodle", "rice", "flour"), "Pasta / Grains"),
    (("cheese", "milk", "yogurt", "butter", "cream", "ice cream", "egg"), "Dairy / Eggs"),
    (("frozen", "pizza", "burrito"), "Frozen Meals"),
    (("beef", "pork", "chicken", "turkey", "ham", "sausage", "hot dog", "bacon", "salami", "deli", "meat"), "Meat / Deli"),
    (("salmon", "tuna", "fish", "shrimp", "crab", "seafood"), "Seafood"),
    (("bean", "oil", "sugar", "pancake", "baking mix", "vinegar"), "Pantry Staples"),
    (("lettuce", "spinach", "romaine", "onion", "tomato", "tomatoes", "potatoes", "pepper", "mushroom", "cantaloupe", "melon",
      "fruit", "salad", "cucumber", "sprout", "produce", "vegetable"), "Fresh Produce"),
    (("juice", "tea", "coffee", "drink", "beverage", "water", "soda", "kombucha"), "Beverages"),
    (("vitamin", "multivitamin", "supplement", "protein powder", "capsule"), "Supplements"),
    (("toothpaste", "mouthwash", "shampoo", "soap", "deodorant", "lotion", "sunscreen", "sanitizer",
      "bandage", "pain reliever", "cough", "floss"), "Personal Care / OTC"),
)

OTHER_FAMILY = "Other Product"


def _stem(token: str) -> str:
    return token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token


def _stem_text(text: str) -> str:
    return " " + " ".join(_stem(t) for t in re.findall(r"[a-z0-9]+", str(text or "").lower())) + " "


def _keyword_in(keyword: str, stemmed_text: str) -> bool:
    return _stem_text(keyword) in stemmed_text


def product_family(text: str) -> str:
    """Map a product name or recall product title to a product family (first matching rule wins)."""
    stemmed = _stem_text(text)
    for keywords, family in PRODUCT_FAMILY_RULES:
        if any(_keyword_in(keyword, stemmed) for keyword in keywords):
            return family
    return OTHER_FAMILY


def catalog_family_products(catalog: pd.DataFrame, family: str) -> pd.DataFrame:
    """Internal catalogue items in the family, found by applying the same rules to their names."""
    if catalog is None or catalog.empty or not family or family == OTHER_FAMILY:
        return catalog.iloc[0:0] if catalog is not None else pd.DataFrame()
    families = catalog["Product"].astype(str).apply(product_family)
    return catalog[families == family]


def family_exposure_estimate(
    recall_item: Dict[str, Any],
    family: str,
    family_products: pd.DataFrame,
    store_master: pd.DataFrame,
    store_inventory: pd.DataFrame,
    dc_inventory: pd.DataFrame,
    dc_network: pd.DataFrame,
) -> Dict[str, Any]:
    """Planning estimate for a family match: footprint stores and DCs holding items of the same family.

    Product-level stock only, no lot split, and labelled as an estimate everywhere it is shown.
    """
    states, known = parse_distribution_states(str(recall_item.get("distribution_pattern") or ""))
    result: Dict[str, Any] = {
        "family_estimate_available": False,
        "family_store_ids": [],
        "family_stores_carrying": 0,
        "family_store_lines": [],
        "family_dc_lines": [],
        "family_estimated_store_on_hand": 0,
        "family_estimated_dc_on_hand": 0,
        "family_estimated_dc_in_transit": 0,
        "family_estimated_value": 0.0,
    }
    if not known or family_products.empty:
        return result
    upcs = set(family_products["UPC"].astype(str))
    prices = dict(zip(family_products["UPC"].astype(str), pd.to_numeric(family_products["Unit Price"], errors="coerce").fillna(0.0)))
    names = dict(zip(family_products["UPC"].astype(str), family_products["Product"].astype(str)))

    in_footprint = store_master[store_master["State"].astype(str).str.upper().isin(states)]
    store_names = dict(zip(in_footprint["Store ID"].astype(str), in_footprint.get("Store Name", in_footprint["Store ID"]).astype(str)))
    store_states = dict(zip(in_footprint["Store ID"].astype(str), in_footprint["State"].astype(str)))
    stock = store_inventory[
        store_inventory["UPC"].astype(str).isin(upcs)
        & store_inventory["Store ID"].astype(str).isin(store_names)
    ].copy()
    stock["On Hand"] = pd.to_numeric(stock["On Hand"], errors="coerce").fillna(0)
    stock = stock[stock["On Hand"] > 0]

    dc_ids = set(dc_network.loc[dc_network["State"].astype(str).str.upper().isin(states), "DC ID"].astype(str))
    dc_stock = dc_inventory[dc_inventory["UPC"].astype(str).isin(upcs) & dc_inventory["DC ID"].astype(str).isin(dc_ids)].copy()
    dc_stock["On Hand"] = pd.to_numeric(dc_stock["On Hand"], errors="coerce").fillna(0)
    dc_stock["In Transit"] = pd.to_numeric(dc_stock["In Transit"], errors="coerce").fillna(0)

    if stock.empty and dc_stock.empty:
        return result

    store_lines: List[Dict[str, Any]] = [
        {
            "Store ID": str(row["Store ID"]),
            "Store Name": store_names.get(str(row["Store ID"]), str(row["Store ID"])),
            "State": store_states.get(str(row["Store ID"]), ""),
            "Family Item": names.get(str(row["UPC"]), ""),
            "Internal UPC (Synthetic)": str(row["UPC"]),
            "On Hand (est.)": int(round(float(row["On Hand"]))),
        }
        for _, row in stock.sort_values(["Store ID", "UPC"]).iterrows()
    ]
    dc_lines: List[Dict[str, Any]] = [
        {
            "DC ID": str(row["DC ID"]),
            "Family Item": names.get(str(row["UPC"]), ""),
            "Internal UPC (Synthetic)": str(row["UPC"]),
            "On Hand (est.)": int(round(float(row["On Hand"]))),
            "In Transit (est.)": int(round(float(row["In Transit"]))),
        }
        for _, row in dc_stock.sort_values(["DC ID", "UPC"]).iterrows()
    ]
    store_on_hand = int(round(float(stock["On Hand"].sum())))
    dc_on_hand = int(round(float(dc_stock["On Hand"].sum())))
    dc_in_transit = int(round(float(dc_stock["In Transit"].sum())))
    value = float((stock["On Hand"] * stock["UPC"].astype(str).map(prices)).sum())
    value += float(((dc_stock["On Hand"] + dc_stock["In Transit"]) * dc_stock["UPC"].astype(str).map(prices)).sum())
    result.update(
        {
            "family_estimate_available": True,
            "family_store_ids": sorted(stock["Store ID"].astype(str).unique().tolist()),
            "family_stores_carrying": int(stock["Store ID"].astype(str).nunique()),
            "family_store_lines": store_lines,
            "family_dc_lines": dc_lines,
            "family_estimated_store_on_hand": store_on_hand,
            "family_estimated_dc_on_hand": dc_on_hand,
            "family_estimated_dc_in_transit": dc_in_transit,
            "family_estimated_value": round(value, 2),
        }
    )
    return result


def family_store_statement(match: Dict[str, Any]) -> str:
    """The one sentence a planner reads about a family match: same-family stock in the footprint, none confirmed."""
    family = str(match.get("product_family", "") or "this product family")
    if not match.get("family_estimate_available"):
        return f"No footprint store carries {family} items; 0 are confirmed affected."
    count = int(match.get("family_stores_carrying", 0) or 0)
    verb = "store carries" if count == 1 else "stores carry"
    return (
        f"{count} footprint {verb} {family} items; 0 are confirmed affected because the recalled product "
        "has not been matched to an internal UPC and lot."
    )


def match_product_family(
    recall_item: Dict[str, Any],
    product_title_text: str,
    catalog: pd.DataFrame,
    store_master: pd.DataFrame,
    store_inventory: pd.DataFrame,
    dc_inventory: pd.DataFrame,
    dc_network: pd.DataFrame,
) -> Optional[Dict[str, Any]]:
    """Analog tier: family of the recall title -> same-family internal items -> footprint estimate.

    Returns None (fall through to the category/hazard tier) when the title maps to no family, the
    retailer carries nothing in that family, or no footprint store or DC holds it -- the same way
    weather drops to Operational Exposure when the analog family has no supporting cases.
    """
    family = product_family(product_title_text)
    if family == OTHER_FAMILY:
        return None
    members = catalog_family_products(catalog, family)
    if members.empty:
        return None
    estimate = family_exposure_estimate(recall_item, family, members, store_master, store_inventory, dc_inventory, dc_network)
    if not estimate["family_estimate_available"]:
        return None
    categories = members["Category"].astype(str).value_counts()
    return {
        "product_family": family,
        "family_products": members["Product"].astype(str).tolist(),
        "family_category": str(categories.index[0]) if not categories.empty else "",
        **estimate,
    }
