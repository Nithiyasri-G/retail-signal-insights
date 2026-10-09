from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "data" / "fixtures" / "v1"
OUTPUT.mkdir(parents=True, exist_ok=True)
os.environ["MARKET_INTELLIGENCE_DB"] = str(OUTPUT / "fixture_export.db")

from market_intelligence.data import demo as _demo  # noqa: E402  (needs sys.path/env set above)

namespace = vars(_demo)
stores = namespace["demo_store_master"]()
dcs = namespace["demo_dc_network"]()
products = namespace["demo_product_catalog"]()
store_inventory = namespace["demo_store_inventory"](stores, products)
dc_inventory = namespace["demo_dc_inventory"](dcs, products)
routes = namespace["demo_route_network"](stores, dcs)
staffing = namespace["demo_employee_schedule"](stores)
assumptions = namespace["scenario_weather_assumptions"]()
market_signals = namespace["demo_market_signals"]()
suppliers = namespace["demo_supplier_master"]()
lots = namespace["demo_lot_master"](products)
sales = namespace["demo_sales_summary"](stores, products, lots)
customers = namespace["demo_customer_purchases"](sales)
substitutes = namespace["demo_substitute_products"]()
staffing_summary = namespace["demo_staffing_summary"](stores)

coordinates = {
    "101": (32.7767, -96.7970), "102": (32.7555, -97.3308),
    "103": (29.7604, -95.3698), "104": (32.7357, -97.1081),
    "105": (33.0198, -96.6989), "106": (30.2672, -97.7431),
    "107": (29.4241, -98.4936), "108": (31.7619, -106.4850),
    "201": (33.7490, -84.3880), "202": (33.7748, -84.2963),
    "203": (33.9526, -84.5499), "204": (32.0809, -81.0912),
    "205": (33.4735, -82.0105), "206": (32.8407, -83.6324),
    "207": (32.4610, -84.9877), "208": (33.9519, -83.3576),
}
stores["Latitude"] = stores["Store ID"].map(lambda value: coordinates[str(value)][0])
stores["Longitude"] = stores["Store ID"].map(lambda value: coordinates[str(value)][1])

captured_at = "2026-09-23T00:00:00+00:00"
store_inventory["Captured At"] = captured_at
store_inventory["Committed"] = 0
store_inventory["Safety Stock"] = 0
store_inventory["Eligible Inbound Before Cutoff"] = store_inventory["Inbound"]
store_inventory["ATP"] = store_inventory["On Hand"] + store_inventory["Eligible Inbound Before Cutoff"]
dc_inventory["Committed"] = (dc_inventory["On Hand"] - dc_inventory["Available to Push"]).clip(lower=0)
dc_inventory["Safety Stock"] = 0
dc_inventory["Eligible Inbound Before Cutoff"] = 0
dc_inventory["ATP"] = dc_inventory["Available to Push"]
dc_inventory["Captured At"] = captured_at
routes["ETA Hours"] = routes["Transit Hours"]
routes["Eligible"] = True
routes["Captured At"] = captured_at

lanes = []
for _, store in stores.iterrows():
    primary = str(store["Nearby DC ID"])
    backup = str(dcs.loc[dcs["DC ID"] == primary, "Backup DC ID"].iloc[0])
    transit = float(routes.loc[routes["Store ID"] == store["Store ID"], "Transit Hours"].iloc[0])
    for _, product in products.iterrows():
        for rank, dc_id, level, extra in ((1, primary, "primary", 0.0), (2, backup, "backup", 2.0)):
            lanes.append(
                {
                    "Store ID": store["Store ID"],
                    "UPC": product["UPC"],
                    "DC ID": dc_id,
                    "Rank": rank,
                    "Transit Hours": transit + extra,
                    "Service Level": level,
                    "Active": True,
                }
            )

tables: dict[str, pd.DataFrame] = {
    "stores": stores,
    "dc_network": dcs,
    "store_sku_dc_lanes": pd.DataFrame(lanes),
    "products": products,
    "store_inventory": store_inventory,
    "dc_inventory": dc_inventory,
    "routes": routes,
    "staffing": staffing,
    "weather_assumptions": assumptions,
    "market_signals": market_signals,
    "suppliers": suppliers,
    "lots": lots,
    "sales_summary": sales,
    "customer_purchases": customers,
    "substitutes": substitutes,
    "staffing_summary": staffing_summary,
}

files = {}
for name, frame in tables.items():
    path = OUTPUT / f"{name}.csv"
    frame.to_csv(path, index=False)
    files[path.name] = {
        "rows": len(frame),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }

manifest = {
    "schema_version": "1.0.0",
    "dataset_version": "v1",
    "generation_method": "Deterministic export of reviewed POC generators",
    "owner": "Market Intelligence POC team",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "provenance": "synthetic",
    "files": files,
}
(OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

(OUTPUT / "fixture_export.db").unlink(missing_ok=True)
