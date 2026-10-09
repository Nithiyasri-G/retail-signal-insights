"""Generate the recall-matching fixture set (synthetic, deterministic).

The weather workflow uses data/fixtures/v1 and is left untouched. Recall matching needs a wider
grocery-style assortment, so this set holds the original 21 v1 items plus generic added items.
Added items use fictional private-label brands and vendors only: no real brand, firm or product
from any live recall. Store assortment is varied on purpose (about one store in four does not carry
a given item), so "stores carrying the candidate product" is a real count, not always all stores.

Run from the project root:  python data/fixtures/recall_v1/generate_recall_fixtures.py
"""
import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
V1 = ROOT / "data" / "fixtures" / "v1"
OUT = ROOT / "data" / "fixtures" / "recall_v1"
CAPTURED = "2026-09-23T00:00:00+00:00"

FOOD, HOUSE, CARE = "Food / Consumables", "Household", "Personal Care"

# (vendor id, vendor name, brand, prior recalls)
VENDORS = [
    ("SUP-9", "Meadowbrook Foods", "Meadowbrook", 0),
    ("SUP-10", "Sunridge Provisions", "Sunridge", 1),
    ("SUP-11", "Harvest Hollow Co", "Harvest Hollow", 0),
    ("SUP-12", "Blue Ridge Bakery Co", "Blue Ridge", 2),
    ("SUP-13", "Lone Star Pantry", "Lone Star Pantry", 0),
    ("SUP-14", "Peachtree Fresh Produce", "Peachtree Fresh", 1),
    ("SUP-15", "Prairie Dairy Cooperative", "Prairie Gold", 0),
    ("SUP-16", "Golden Grain Mills", "Golden Grain", 0),
    ("SUP-17", "Cedar Kitchen Foods", "Cedar Kitchen", 3),
    ("SUP-18", "Summit Snack Co", "Summit", 0),
    ("SUP-19", "Bright Home Supply", "Bright Home", 1),
    ("SUP-20", "Wellspring Health Products", "Wellspring", 2),
]
V = {v[0]: v for v in VENDORS}

# (product, category, vendor id, unit price, package size, lot tracked)
ITEMS = [
    # Bakery
    ("Italian Bread", FOOD, "SUP-12", 3.29, "12 oz", True), ("French Bread", FOOD, "SUP-12", 3.19, "10 oz", True),
    ("Sourdough Bread", FOOD, "SUP-12", 4.49, "16 oz", True), ("Potato Bread", FOOD, "SUP-12", 3.99, "20 oz", False),
    ("Sandwich Bread", FOOD, "SUP-12", 2.89, "20 oz", True), ("Bread Pudding", FOOD, "SUP-12", 5.49, "13.8 oz", True),
    ("Shortbread Cookies", FOOD, "SUP-12", 4.29, "8 oz", True), ("Chocolate Chip Cookies", FOOD, "SUP-12", 3.99, "12 oz", False),
    ("Oatmeal Cookies", FOOD, "SUP-12", 3.79, "12 oz", False), ("Pumpkin Spice Cookies", FOOD, "SUP-12", 4.19, "3.5 oz", False),
    ("Dinner Rolls", FOOD, "SUP-12", 3.49, "12 ct", False), ("Tortillas", FOOD, "SUP-13", 2.99, "10 ct", False),
    # Produce and fresh prepared
    ("Fresh Salsa", FOOD, "SUP-14", 3.99, "16 oz", True), ("Pico de Gallo", FOOD, "SUP-14", 3.79, "12 oz", True),
    ("Guacamole", FOOD, "SUP-14", 4.49, "8 oz", True), ("Hummus", FOOD, "SUP-14", 3.49, "10 oz", False),
    ("Salad Kit", FOOD, "SUP-14", 4.29, "10 oz", True), ("Spinach", FOOD, "SUP-14", 2.99, "10 oz", True),
    ("Romaine Lettuce", FOOD, "SUP-14", 2.49, "3 ct", True), ("Cantaloupe", FOOD, "SUP-14", 3.49, "each", False),
    ("Cut Fruit Cup", FOOD, "SUP-14", 3.29, "12 oz", True), ("Diced Onions", FOOD, "SUP-14", 2.79, "10 oz", False),
    ("Jalapeno Peppers", FOOD, "SUP-14", 1.99, "8 oz", False), ("Green Bell Peppers", FOOD, "SUP-14", 2.49, "3 ct", False),
    ("Roma Tomatoes", FOOD, "SUP-14", 2.99, "1 lb", False), ("Sliced Mushrooms", FOOD, "SUP-14", 2.79, "8 oz", False),
    # Dairy and eggs
    ("Cream Cheese", FOOD, "SUP-15", 2.99, "8 oz", True), ("Cheddar Cheese", FOOD, "SUP-15", 4.49, "8 oz", True),
    ("Shredded Cheese", FOOD, "SUP-15", 3.99, "8 oz", True), ("Greek Yogurt", FOOD, "SUP-15", 1.29, "5.3 oz", False),
    ("Whole Milk", FOOD, "SUP-15", 3.79, "1 gal", True), ("Large Eggs", FOOD, "SUP-15", 3.49, "12 ct", True),
    ("Salted Butter", FOOD, "SUP-15", 4.29, "16 oz", False), ("Vanilla Ice Cream", FOOD, "SUP-15", 5.49, "48 oz", False),
    # Meat, deli, frozen
    ("Deli Turkey", FOOD, "SUP-17", 6.99, "8 oz", True), ("Sliced Ham", FOOD, "SUP-17", 5.99, "8 oz", True),
    ("Beef Hot Dogs", FOOD, "SUP-17", 4.49, "14 oz", False), ("Pork Sausage", FOOD, "SUP-17", 4.99, "12 oz", False),
    ("Ground Beef", FOOD, "SUP-17", 6.49, "1 lb", True), ("Chicken Nuggets", FOOD, "SUP-17", 7.49, "32 oz", False),
    ("Frozen Pizza", FOOD, "SUP-17", 6.49, "22 oz", False), ("Frozen Burritos", FOOD, "SUP-13", 5.49, "8 ct", False),
    ("Frozen Mixed Vegetables", FOOD, "SUP-13", 1.99, "12 oz", False), ("Smoked Salmon", FOOD, "SUP-17", 8.99, "4 oz", True),
    # Soups, sauces, canned, pantry
    ("Tomato Soup", FOOD, "SUP-10", 1.99, "14.5 oz", True), ("Chicken Noodle Soup", FOOD, "SUP-10", 2.19, "14.5 oz", False),
    ("Lentil Soup", FOOD, "SUP-10", 2.29, "14.5 oz", False), ("Soup Mix", FOOD, "SUP-10", 3.49, "16 oz", False),
    ("Tomato Bisque Soup Kit", FOOD, "SUP-10", 6.99, "24 oz", True), ("Marinara Sauce", FOOD, "SUP-10", 2.99, "24 oz", False),
    ("Alfredo Sauce", FOOD, "SUP-10", 3.29, "15 oz", False), ("Taco Sauce", FOOD, "SUP-13", 1.99, "8 oz", False),
    ("Hot Sauce", FOOD, "SUP-13", 2.49, "5 oz", False), ("Salsa Verde", FOOD, "SUP-13", 3.29, "16 oz", True),
    ("Canned Tuna", FOOD, "SUP-10", 1.49, "5 oz", False), ("Canned Black Beans", FOOD, "SUP-10", 1.19, "15 oz", False),
    ("Canned Diced Tomatoes", FOOD, "SUP-10", 1.29, "14.5 oz", False), ("Chicken Broth", FOOD, "SUP-10", 2.29, "32 oz", False),
    ("Chicken Bouillon", FOOD, "SUP-10", 3.49, "8 oz", False), ("Seasoning Packets", FOOD, "SUP-13", 1.29, "1 oz", False),
    ("Soy Sauce", FOOD, "SUP-10", 2.99, "10 oz", False), ("Soybean Oil", FOOD, "SUP-16", 4.99, "48 oz", False),
    ("Olive Oil", FOOD, "SUP-16", 8.99, "17 oz", False), ("Long Grain Rice", FOOD, "SUP-16", 3.49, "2 lb", False),
    ("Spaghetti Pasta", FOOD, "SUP-16", 1.49, "16 oz", False), ("Macaroni and Cheese", FOOD, "SUP-16", 1.19, "7.25 oz", False),
    ("Pancake Mix", FOOD, "SUP-16", 3.29, "32 oz", False), ("All Purpose Flour", FOOD, "SUP-16", 3.49, "5 lb", False),
    ("Granulated Sugar", FOOD, "SUP-16", 3.29, "4 lb", False), ("Maple Syrup", FOOD, "SUP-11", 7.99, "12 oz", False),
    ("Wildflower Honey", FOOD, "SUP-11", 5.99, "12 oz", False), ("Strawberry Jam", FOOD, "SUP-11", 3.49, "18 oz", False),
    ("Almond Butter", FOOD, "SUP-11", 7.49, "12 oz", True),
    # Cereal, snacks, candy, drinks
    ("Granola", FOOD, "SUP-16", 4.99, "12 oz", True), ("Corn Flakes Cereal", FOOD, "SUP-16", 3.79, "18 oz", False),
    ("Instant Oatmeal", FOOD, "SUP-16", 3.29, "10 ct", False), ("Tortilla Chips", FOOD, "SUP-18", 3.29, "13 oz", False),
    ("Potato Chips", FOOD, "SUP-18", 3.49, "8 oz", False), ("Pretzels", FOOD, "SUP-18", 2.79, "16 oz", False),
    ("Microwave Popcorn", FOOD, "SUP-18", 3.29, "6 ct", False), ("Saltine Crackers", FOOD, "SUP-18", 2.49, "16 oz", False),
    ("Hard Candy", FOOD, "SUP-18", 2.99, "7 oz", False), ("Chocolate Bars", FOOD, "SUP-18", 1.49, "1.5 oz", False),
    ("Gummy Candy", FOOD, "SUP-18", 2.49, "8 oz", False), ("Fruit Snacks", FOOD, "SUP-18", 3.49, "10 ct", False),
    ("Orange Juice", FOOD, "SUP-11", 3.99, "52 oz", False), ("Apple Juice", FOOD, "SUP-11", 3.49, "64 oz", False),
    ("Green Tea", FOOD, "SUP-11", 3.29, "20 ct", False), ("Ground Coffee", FOOD, "SUP-11", 7.99, "12 oz", False),
    ("Sports Drink", FOOD, "SUP-11", 1.79, "28 oz", False), ("Protein Powder", FOOD, "SUP-20", 24.99, "2 lb", True),
    ("Infant Cereal", FOOD, "SUP-20", 3.99, "8 oz", True), ("Toddler Snacks", FOOD, "SUP-20", 3.29, "1.5 oz", True),
    # Household
    ("Dish Soap", HOUSE, "SUP-19", 2.99, "19 oz", False), ("All Purpose Cleaner", HOUSE, "SUP-19", 3.49, "32 oz", False),
    ("Trash Bags", HOUSE, "SUP-19", 8.99, "30 ct", False), ("Food Storage Bags", HOUSE, "SUP-19", 3.99, "40 ct", False),
    ("Aluminum Foil", HOUSE, "SUP-19", 4.49, "75 sq ft", False), ("Paper Napkins", HOUSE, "SUP-19", 2.99, "200 ct", False),
    ("Light Bulbs", HOUSE, "SUP-19", 5.99, "4 ct", False), ("Food Coloring", HOUSE, "SUP-19", 3.49, "1 oz", False),
    ("Dishwasher Pods", HOUSE, "SUP-19", 9.99, "40 ct", False),
    # Personal care
    ("Mouthwash", CARE, "SUP-20", 4.99, "16 oz", False), ("Deodorant", CARE, "SUP-20", 3.99, "2.6 oz", False),
    ("Body Lotion", CARE, "SUP-20", 5.49, "16 oz", False), ("Sunscreen", CARE, "SUP-20", 8.99, "8 oz", True),
    ("Adhesive Bandages", CARE, "SUP-20", 3.49, "50 ct", False), ("Pain Reliever", CARE, "SUP-20", 7.99, "100 ct", True),
    ("Cough Syrup", CARE, "SUP-20", 6.99, "8 oz", True), ("Multivitamins", CARE, "SUP-20", 9.99, "90 ct", True),
    ("Dental Floss", CARE, "SUP-20", 2.49, "50 yd", False), ("Bar Soap", CARE, "SUP-20", 3.29, "4 ct", False),
]

SIZE_NOTE = "Fictional private-label item for the recall-matching demo."


def seed(*parts: str) -> int:
    return int(hashlib.md5("-".join(str(p) for p in parts).encode()).hexdigest(), 16)


def upc_a(index: int) -> str:
    body = "09" + f"{900 + index // 100:03d}" + f"{index % 100:02d}" + f"{(index * 7) % 100:02d}"
    body = body[:11].ljust(11, "0")
    digits = [int(c) for c in body]
    check = (10 - ((sum(digits[0::2]) * 3 + sum(digits[1::2])) % 10)) % 10
    return body + str(check)


def main() -> None:
    products = pd.read_csv(V1 / "products.csv", dtype={"UPC": str})
    suppliers = pd.read_csv(V1 / "suppliers.csv")
    lots = pd.read_csv(V1 / "lots.csv", dtype={"UPC": str})
    store_inv = pd.read_csv(V1 / "store_inventory.csv", dtype={"Store ID": str, "UPC": str})
    dc_inv = pd.read_csv(V1 / "dc_inventory.csv", dtype={"UPC": str})
    sales = pd.read_csv(V1 / "sales_summary.csv", dtype={"Store ID": str, "UPC": str})
    purchases = pd.read_csv(V1 / "customer_purchases.csv", dtype={"Store ID": str})
    substitutes = pd.read_csv(V1 / "substitutes.csv")
    stores = pd.read_csv(V1 / "stores.csv", dtype={"Store ID": str})["Store ID"].tolist()
    dcs = pd.read_csv(V1 / "dc_network.csv")["DC ID"].tolist()

    # Original items keep every existing row; only descriptive columns are added.
    original_vendor = dict(zip(suppliers["Supplier ID"], suppliers["Supplier Name"]))
    products["Brand"] = products["Supplier ID"].map(original_vendor)
    products["Package Size"] = ""
    products["Description"] = products["Product"] + " (original demo item)"

    new_products, new_lots, new_store, new_dc, new_sales, new_buy = [], [], [], [], [], []
    seen_names = set(products["Product"])
    for index, (name, category, vendor_id, price, size, lot_tracked) in enumerate(ITEMS, start=1):
        if name in seen_names:
            continue
        upc = upc_a(index)
        vendor = V[vendor_id]
        case_pack = 6 if price >= 3 else 12
        new_products.append({
            "UPC": upc, "Product": name, "Category": category, "Supplier ID": vendor_id, "Lot Tracked": lot_tracked,
            "Unit Price": price, "Case Pack": case_pack, "MOQ": case_pack * 2, "Sellable Unit": "EA",
            "Brand": vendor[2], "Package Size": size,
            "Description": f"{vendor[2]} {name}, {size}. {SIZE_NOTE}",
        })
        lot_ids = []
        if lot_tracked:
            for suffix, made in (("A", "2026-06-15"), ("B", "2026-07-20")):
                lot_id = f"LOT-{upc[-5:-1]}-{suffix}"
                lot_ids.append(lot_id)
                new_lots.append({"Lot ID": lot_id, "UPC": upc, "Product": name, "Manufacture Date": made})
        for store in stores:
            if seed("carry", store, upc) % 4 == 0:
                continue  # this store does not carry the item
            on_hand = 20 + seed("oh", store, upc) % 230
            inbound = seed("in", store, upc) % 60
            baseline = on_hand + inbound + seed("bl", store, upc) % 40
            new_store.append({
                "Store ID": store, "UPC": upc, "Product": name, "Category": category, "Case Pack": case_pack,
                "MOQ": case_pack * 2, "Sellable Unit": "EA", "Baseline Forecast": baseline,
                "Historical Average": max(baseline - 10, 0), "On Hand": on_hand, "Inbound": inbound,
                "Captured At": CAPTURED, "Committed": 0, "Safety Stock": 0,
                "Eligible Inbound Before Cutoff": inbound, "ATP": on_hand + inbound,
            })
            for lot_id in lot_ids:
                units = 15 + seed("sold", store, lot_id) % 120
                new_sales.append({"Store ID": store, "UPC": upc, "Product": name, "Lot ID": lot_id,
                                  "Units Sold": units, "Period": "Last 90 days"})
                for token in range(2):
                    new_buy.append({
                        "Store ID": store, "Product": name, "Lot ID": lot_id,
                        "Customer Token": f"CUST-TOK-{seed('tok', store, lot_id, token) % 90000 + 10000}",
                        "Units": 1 + seed("u", store, lot_id, token) % 6,
                        "Exposure Type": "Loyalty-linked" if token == 0 else "Anonymous (no loyalty link)",
                    })
        for dc in dcs:
            on_hand = 300 + seed("dcoh", dc, upc) % 1200
            in_transit = 20 + seed("dcit", dc, upc) % 150
            reserved = int(on_hand * 0.8)
            push = on_hand - reserved
            new_dc.append({
                "DC ID": dc, "UPC": upc, "Product": name, "On Hand": on_hand, "In Transit": in_transit,
                "Available to Push": push, "Reserved": reserved, "Safety Stock": 0, "ATP": push,
                "Captured At": CAPTURED, "Committed": reserved, "Eligible Inbound Before Cutoff": 0,
            })

    products_out = pd.concat([products, pd.DataFrame(new_products)], ignore_index=True)
    suppliers_out = pd.concat(
        [suppliers, pd.DataFrame([{"Supplier ID": v[0], "Supplier Name": v[1], "Prior Recall Count": v[3]} for v in VENDORS])],
        ignore_index=True,
    )
    subs = pd.DataFrame([
        {"Product": "Italian Bread", "Substitute Product": "French Bread"},
        {"Product": "French Bread", "Substitute Product": "Sourdough Bread"},
        {"Product": "Fresh Salsa", "Substitute Product": "Salsa Verde"},
        {"Product": "Granola", "Substitute Product": "Trail Mix"},
    ])
    tables = {
        "products": products_out,
        "suppliers": suppliers_out,
        "lots": pd.concat([lots, pd.DataFrame(new_lots)], ignore_index=True),
        "store_inventory": pd.concat([store_inv, pd.DataFrame(new_store)], ignore_index=True),
        "dc_inventory": pd.concat([dc_inv, pd.DataFrame(new_dc)], ignore_index=True),
        "sales_summary": pd.concat([sales, pd.DataFrame(new_sales)], ignore_index=True),
        "customer_purchases": pd.concat([purchases, pd.DataFrame(new_buy)], ignore_index=True),
        "substitutes": pd.concat([substitutes, subs], ignore_index=True),
    }
    files = {}
    for name, frame in tables.items():
        path = OUT / f"{name}.csv"
        frame.to_csv(path, index=False)
        files[f"{name}.csv"] = {"rows": len(frame), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (OUT / "manifest.json").write_text(json.dumps({
        "schema_version": "1.0.0",
        "dataset_version": "recall_v1",
        "generation_method": "Deterministic: original v1 rows plus generic fictional grocery items (generate_recall_fixtures.py)",
        "owner": "Market Intelligence POC team",
        "provenance": "synthetic",
        "files": files,
    }, indent=2), encoding="utf-8")
    print({k: v["rows"] for k, v in files.items()})


if __name__ == "__main__":
    main()
