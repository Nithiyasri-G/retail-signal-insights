from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any
from typing import Dict
from typing import Optional
import pandas as pd

from market_intelligence.data.demo import demo_lot_master, demo_product_catalog, demo_recall_catalog, demo_store_master, scenario_weather_assumptions
from market_intelligence.util.clock import utc_now


DEMO_SCENARIO_WEATHER_EVENT_TYPES = sorted(scenario_weather_assumptions()["Event Type"].unique().tolist())


DEMO_SCENARIO_STATES = sorted(demo_store_master()["State"].unique().tolist())


def demo_weather_alert_scenario(
    event_type: str,
    state: str,
    store_master: Optional[pd.DataFrame] = None,
    county: Optional[str] = None,
    headline: Optional[str] = None,
    severity: str = "Severe",
) -> Dict[str, Any]:
    """Build a clearly-labelled controlled weather signal shaped like one NOAA alert.

    The user supplies only Event Type + State. Store, region, scenario-case count,
    inventory, DC coverage, staffing exposure, and actions are all derived downstream.
    A 36-hour lead window and ~30-hour duration are demonstration assumptions so the
    route/replenishment logic can show *when* inventory should move, as in the use-case.
    """
    store_master = store_master if store_master is not None else demo_store_master()
    state_stores = store_master[store_master["State"] == state]
    if county is None:
        county = state_stores.iloc[0]["County"] if not state_stores.empty else ""
    area_desc = f"{county.replace(' County', '')}, {state} (Demonstration scenario)" if county else f"{state} (Demonstration scenario)"
    now = datetime.now(timezone.utc)
    onset = now + timedelta(hours=36)
    ends = onset + timedelta(hours=30)
    signal = {
        "date": utc_now()[:10],
        "retailer": "Demonstration",
        "region": state,
        "region_scope": "state_weather_alerts",
        "source": "Demonstration scenario -- not a live NOAA alert",
        "signal_area": "Weather Risk",
        "signal_name": "supply_chain_weather_risk_score",
        "signal_value": 1,
        "risk_score": 7.0,
        "confidence": "Demonstration",
        "score_reason": "Controlled demonstration scenario generated on request; not a live NOAA alert.",
        "business_impact": "Demonstration scenario for workflow testing only.",
        "recommended_action": "N/A -- demonstration scenario.",
        "raw_reference": "Demonstration scenario",
        "is_demo_scenario": True,
    }
    item = {
        "alert_id": f"DEMO-{event_type}-{state}-{county}",
        "event": event_type,
        "severity": severity,
        "urgency": "Expected",
        "certainty": "Likely",
        "headline": headline or f"[Demonstration scenario] {event_type} for {state}",
        "area_desc": area_desc,
        "effective": now.isoformat(),
        "onset": onset.isoformat(),
        "expires": ends.isoformat(),
        "ends": ends.isoformat(),
        "instruction": "",
        "risk_component": 3.0,
    }
    return {"status": "success", "source": "Demonstration scenario", "error": "", "raw": None, "rows": [signal], "items": [item]}


def demo_recall_probable_scenario(
    product_name: str,
    classification: str = "Class II",
    recalling_firm: Optional[str] = None,
) -> Dict[str, Any]:
    """A controlled recall shaped like a real openFDA record: product title, no UPC and no lot.

    It exercises the probable-product-match path (title match, candidate stores, planning estimate) and can
    never reach "confirmed", exactly like a live recall that lacks a clean UPC and lot.
    """
    catalog = demo_recall_catalog()
    row = catalog[catalog["Product"] == product_name].iloc[0]
    brand = str(row.get("Brand", "") or "")
    size = str(row.get("Package Size", "") or "")
    title = f"{brand} {product_name}".strip() + (f", {size}" if size else "")
    return {
        "product": f"{title}. Ingredients: demonstration list. [Demonstration scenario -- not a live FDA recall]",
        "reason": "Controlled demonstration recall for the probable-match workflow.",
        "state": "Multi-state",
        "classification": classification,
        "status": "Ongoing",
        "recall_date": utc_now()[:10],
        "distribution_pattern": "Texas and Georgia",
        "recalling_firm": recalling_firm or "Demonstration Supplier",
        "upcs": "",
        "lots": "",
        "sku_match_status": "unknown",
        "risk_type": "general_recall_risk",
        "risk_score": 8.0 if classification == "Class I" else 6.0,
        "is_demo_scenario": True,
    }


def demo_recall_scenario(
    product_name: str,
    catalog: Optional[pd.DataFrame] = None,
    lot_master: Optional[pd.DataFrame] = None,
    reason: Optional[str] = None,
    classification: str = "Class II",
    recalling_firm: str = "Demonstration Supplier",
    state: str = "Multi-state",
) -> Dict[str, Any]:
    """Build a synthetic-but-clearly-labeled FDA recall record, shaped exactly like one
    collect_fda_recalls() item, for a guaranteed demo when live openFDA results don't
    happen to match the demonstration catalog's UPCs/lots. Clearly labeled per the
    Operational Impact Plan; never presented as a live FDA recall.

    reason/classification/recalling_firm let a specific demo case (e.g. a named safety
    issue) read as a realistic, specific scenario rather than generic placeholder text.
    """
    catalog = catalog if catalog is not None else demo_product_catalog()
    lot_master = lot_master if lot_master is not None else demo_lot_master(catalog)
    product_row = catalog[catalog["Product"] == product_name].iloc[0]
    matching_lots = lot_master[lot_master["UPC"] == product_row["UPC"]]
    lot_id = matching_lots.iloc[0]["Lot ID"] if not matching_lots.empty else ""
    return {
        "product": f"{product_name} [Demonstration scenario -- not a live FDA recall]",
        "reason": reason or "Controlled demonstration recall generated on request for workflow testing.",
        "state": state,
        "classification": classification,
        "status": "Ongoing",
        "recall_date": utc_now()[:10],
        "distribution_pattern": "Nationwide",
        "recalling_firm": recalling_firm,
        "upcs": product_row["UPC"],
        "lots": lot_id,
        "sku_match_status": "unknown",
        "risk_type": "general_recall_risk",
        "risk_score": 8.0 if classification == "Class I" else 6.0,
        "is_demo_scenario": True,
    }
