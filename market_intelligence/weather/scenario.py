from market_intelligence.data.fixture_repository import FixtureRepository
from market_intelligence.replenishment.allocation import allocate_product_requirement
from market_intelligence.replenishment.quantity import calculate_order_quantity
from market_intelligence.weather.geography import match_alert_to_stores
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
import pandas as pd

from market_intelligence.config.settings import ROUTE_DISRUPTION_PENALTY_HOURS
from market_intelligence.data.demo import demo_dc_inventory, demo_dc_network, demo_employee_schedule, demo_route_network, demo_store_inventory, demo_store_master, scenario_weather_assumptions
from market_intelligence.weather.assumptions import WEATHER_FAMILY_CATEGORY_HINTS, WEATHER_PHASE_ORDER, weather_event_family, weather_scenario_assumption_lookup
from market_intelligence.weather.planning import _geometry_bounding_box, can_arrive_before_deadline, compute_planning_demand, traffic_phase_explanation, weather_alert_geocode_states
from market_intelligence.weather.staffing import evaluate_staffing_exposure


def build_weather_scenario(
    weather_result: Dict[str, Any],
    store_master: Optional[pd.DataFrame] = None,
    dc_network: Optional[pd.DataFrame] = None,
    route_network: Optional[pd.DataFrame] = None,
    store_inventory: Optional[pd.DataFrame] = None,
    dc_inventory: Optional[pd.DataFrame] = None,
    employee_schedule: Optional[pd.DataFrame] = None,
    history: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """Turn one NOAA alert into one operational weather incident.

    Flow: alert geography -> affected store(s) -> versioned scenario assumption set ->
    SKU/product demand uplift -> store inventory gap -> primary/alternate DC capacity and
    alert timing -> current scheduled employee commute exposure -> operational actions.

    County matching is used as the demonstration proxy for a real NOAA polygon/route GIS
    join. State-only scope is retained as an honest evidence limitation rather than being
    treated as a precise store impact.
    """
    store_master = store_master if store_master is not None else demo_store_master()
    dc_network = dc_network if dc_network is not None else demo_dc_network()
    route_network = route_network if route_network is not None else demo_route_network(store_master, dc_network)
    store_inventory = store_inventory if store_inventory is not None else demo_store_inventory(store_master)
    dc_inventory = dc_inventory if dc_inventory is not None else demo_dc_inventory(dc_network)
    employee_schedule = employee_schedule if employee_schedule is not None else demo_employee_schedule(store_master)
    history = history if history is not None else scenario_weather_assumptions()

    rows = weather_result.get("rows") or []
    items = weather_result.get("items") or []
    if not rows or not items:
        return {"status": "no_match", "reason": "No active weather signal to evaluate.", "scenario_df": pd.DataFrame()}

    signal = rows[0]
    top_item = items[0]
    source_query_states = top_item.get("source_query_states") or [signal.get("region", "")]
    state = ", ".join(sorted(set(str(x).upper() for x in source_query_states if x)))
    candidate_states = set(str(x).upper() for x in source_query_states if x)
    # NOAA geocodes carry the alert's own state(s) (2-letter prefix of each UGC/FIPS
    # code); prefer that authoritative source over the queried state(s), which is only
    # where the search started and may miss a multi-state alert.
    geocode_states = weather_alert_geocode_states(top_item.get("geocode"))
    if geocode_states:
        candidate_states |= geocode_states
    elif top_item.get("geometry"):
        # No geocode state hint at all: derive candidate states from the polygon's own
        # bounding box (which Stores could it possibly reach?) rather than sweeping
        # every Store-master state nationwide -- this is what previously caused a
        # single-region alert to report every Store as "evaluated." The exact per-store
        # polygon test still happens later in match_alert_to_stores; this only narrows
        # which states are even considered. Falls back to the full Store universe only
        # if the bounding box contains no Store at all (keeps the alert evaluable rather
        # than silently reporting zero evaluated Stores on a bbox edge case).
        bbox = _geometry_bounding_box(top_item.get("geometry"))
        bbox_states: set = set()
        if bbox:
            min_lon, max_lon, min_lat, max_lat = bbox
            in_bbox = store_master[
                (store_master["Longitude"] >= min_lon)
                & (store_master["Longitude"] <= max_lon)
                & (store_master["Latitude"] >= min_lat)
                & (store_master["Latitude"] <= max_lat)
            ]
            bbox_states = set(in_bbox["State"].astype(str))
        candidate_states = bbox_states or set(store_master["State"].astype(str))
    mapping_states = ", ".join(sorted(candidate_states))
    top_event = str(top_item.get("event", "") or "")
    area_desc_raw = str(top_item.get("area_desc", "") or "")
    area_desc = area_desc_raw.lower()

    state_stores = store_master[store_master["State"].isin(candidate_states)]
    geography_match = match_alert_to_stores(
        area_desc_raw,
        mapping_states,
        store_master.to_dict("records"),
        alert_geometry=top_item.get("geometry"),
        alert_geocodes=top_item.get("geocode"),
    )
    if geography_match.evidence_code == "STORE_COVERAGE_MISSING":
        return {
            "status": "no_match",
            "reason": geography_match.explanation,
            "scenario_df": pd.DataFrame(),
            "state": state,
            "event": top_event,
            "evidence_code": geography_match.evidence_code,
            "geography_match": geography_match.to_dict(),
            "alert_item": top_item,
            "source_query_states": source_query_states,
            "match_type": "Not assessed - no Store match",
            "match_reason": geography_match.explanation,
            "event_family": weather_event_family(top_event),
            "category_hints": WEATHER_FAMILY_CATEGORY_HINTS.get(weather_event_family(top_event), []),
        }

    if geography_match.affected_store_ids:
        affected_stores = state_stores[
            state_stores["Store ID"].astype(str).isin(geography_match.affected_store_ids)
        ]
        match_precision = geography_match.method
        # Ajith requirement: a closed Store inside the alert footprint is acknowledged
        # geographically (it stays in geography_match/affected_store_ids as a plain
        # geographic fact) but must not receive operational actions. Split it out here,
        # before any demand/DC/staffing computation, rather than filtering it out of the
        # visible geography match entirely.
        if "Operating Status" in affected_stores.columns:
            closed_mask = affected_stores["Operating Status"].astype(str) == "Closed"
        else:
            closed_mask = pd.Series(False, index=affected_stores.index)
        closed_stores_in_footprint = affected_stores[closed_mask][["Store ID", "Store Name", "County"]].to_dict("records")
        affected_stores = affected_stores[~closed_mask]
        if affected_stores.empty:
            return {
                "status": "insufficient_evidence",
                "reason": (
                    f"{len(closed_stores_in_footprint)} Store(s) matched this alert's geography but "
                    "are marked Closed; no operational action applies to a closed Store."
                ),
                "scenario_df": pd.DataFrame(),
                "state": state,
                "event": top_event,
                "match_precision": match_precision,
                "affected_stores": [],
                "closed_stores_in_footprint": closed_stores_in_footprint,
                "comparable_events": 0,
                "evidence_code": "STORE_CLOSED_IN_FOOTPRINT",
                "geography_match": geography_match.to_dict(),
                "alert_item": top_item,
                "source_query_states": source_query_states,
                "match_type": "Not assessed - matched Store(s) closed",
                "match_reason": (
                    f"{len(closed_stores_in_footprint)} matched Store(s) are Closed; no operational action applies."
                ),
                "event_family": weather_event_family(top_event),
                "category_hints": WEATHER_FAMILY_CATEGORY_HINTS.get(weather_event_family(top_event), []),
            }
    else:
        # Do not turn all Stores in a state into false "candidate affected Stores."
        return {
            "status": "insufficient_evidence",
            "reason": geography_match.explanation,
            "scenario_df": pd.DataFrame(),
            "state": state,
            "event": top_event,
            "match_precision": "State only (broad scope)",
            "affected_stores": [],
            "evaluated_stores": state_stores[["Store ID", "Store Name", "County"]].to_dict("records"),
            "candidate_stores": state_stores[["Store ID", "Store Name", "County"]].to_dict("records"),
            "comparable_events": 0,
            "evidence_code": geography_match.evidence_code,
            "geography_match": geography_match.to_dict(),
            "alert_item": top_item,
            "source_query_states": source_query_states,
            "match_type": "Not assessed - no Store match",
            "match_reason": geography_match.explanation,
            "event_family": weather_event_family(top_event),
            "category_hints": WEATHER_FAMILY_CATEGORY_HINTS.get(weather_event_family(top_event), []),
        }

    regions = sorted(set(affected_stores["Region"].astype(str)))
    region = regions[0] if regions else ""
    assumptions = weather_scenario_assumption_lookup(top_event, region, history)
    if not assumptions["sufficient_evidence"]:
        return {
            "status": "insufficient_evidence",
            "reason": assumptions.get("match_reason") or (
                f"Only {assumptions['comparable_events']} comparable scenario case(s) exist for "
                f"{top_event} in {region}; at least 3 are required before applying a demand assumption."
            ),
            "scenario_df": pd.DataFrame(),
            "state": state,
            "region": region,
            "event": top_event,
            "match_precision": match_precision,
            "affected_stores": affected_stores[["Store ID", "Store Name", "County"]].to_dict("records"),
            "comparable_events": assumptions["comparable_events"],
            "evidence_code": "OPERATIONAL_EXPOSURE_NO_DEMAND_HISTORY",
            "geography_match": geography_match.to_dict(),
            "alert_item": top_item,
            "source_query_states": source_query_states,
            "match_type": assumptions.get("match_type", "Operational Exposure Match"),
            "match_reason": assumptions.get("match_reason", "Store/DC exposure found, but no sufficient scenario history."),
            "event_family": assumptions.get("event_family", weather_event_family(top_event)),
            "matched_event_types": assumptions.get("matched_event_types", []),
            "category_hints": assumptions.get("category_hints", []),
        }

    onset_raw = top_item.get("onset") or top_item.get("effective") or ""
    ends_raw = top_item.get("ends") or top_item.get("expires") or ""
    onset_ts = pd.to_datetime(onset_raw, utc=True, errors="coerce")
    ends_ts = pd.to_datetime(ends_raw, utc=True, errors="coerce")
    now_ts = pd.to_datetime(weather_result.get("assessment_time"), utc=True, errors="coerce")
    if pd.isna(now_ts):
        now_ts = pd.Timestamp.now(tz="UTC")
    # Full-precision versions are kept separately (below, once has_onset_time/has_end_time
    # exist) for deadline-eligibility math. These rounded copies are for DISPLAY only
    # (e.g. the "Hours Until Alert" column) -- W03: rounding a value before comparing it
    # against a deadline can flip the answer (6h58m rounds to 7.0h and would incorrectly
    # pass a 7h-total-lead-time requirement).
    hours_until_event = round(max(0.0, (onset_ts - now_ts).total_seconds() / 3600.0), 1) if pd.notna(onset_ts) else 0.0
    duration_hours = round(max(0.0, (ends_ts - onset_ts).total_seconds() / 3600.0), 1) if pd.notna(onset_ts) and pd.notna(ends_ts) else 0.0

    # A LIVE alert is frequently already in effect by the time it is picked up (this is
    # especially common for Advisory-tier products): onset is in the past, so
    # hours_until_event clamps to 0.0. The Controlled Demo generator always hardcodes an
    # onset 36 hours in the future, so it can never exercise this branch -- which is how
    # this gap escaped earlier testing. An already-active alert should not be treated as
    # "no time left to replenish"; it should be judged against the time remaining until the
    # alert ENDS instead. If no usable timing field exists at all, timing is unknown and
    # should not silently zero out an otherwise valid DC allocation.
    alert_is_active = bool(pd.notna(onset_ts) and onset_ts <= now_ts)
    hours_until_ends = (
        round(max(0.0, (ends_ts - now_ts).total_seconds() / 3600.0), 1)
        if pd.notna(ends_ts)
        else 0.0
    )
    has_end_time = pd.notna(ends_ts)
    has_onset_time = pd.notna(onset_ts)
    # Full precision, never rounded, for the deadline comparison in _can_arrive_in_time.
    hours_until_event_precise = max(0.0, (onset_ts - now_ts).total_seconds() / 3600.0) if has_onset_time else 0.0
    hours_until_ends_precise = max(0.0, (ends_ts - now_ts).total_seconds() / 3600.0) if has_end_time else 0.0

    # Explicit three-state lifecycle -- not a binary active/inactive gate. An alert
    # whose own end time has already passed is Expired and must not be treated as a
    # live, executable event (it was previously indistinguishable from "Active" with
    # hours_until_ends clamped to 0.0). Checked in this order because an ended alert is
    # Expired regardless of whether its onset time happens to be known; `alert_is_active`
    # is kept as-is (used elsewhere for the Active/Upcoming timing split) but is no
    # longer sufficient on its own to decide whether a plan may still be produced.
    if has_end_time and ends_ts <= now_ts:
        alert_lifecycle = "Expired"
    elif alert_is_active:
        alert_lifecycle = "Active"
    else:
        alert_lifecycle = "Upcoming"

    if alert_lifecycle == "Expired":
        return {
            "status": "insufficient_evidence",
            "reason": "This alert's own end time has already passed; it cannot produce a new executable replenishment plan.",
            "scenario_df": pd.DataFrame(),
            "state": state,
            "region": region,
            "event": top_event,
            "match_precision": match_precision,
            "affected_stores": affected_stores[["Store ID", "Store Name", "County"]].to_dict("records"),
            "comparable_events": assumptions.get("comparable_events", 0),
            "evidence_code": "ALERT_EXPIRED",
            "geography_match": geography_match.to_dict(),
            "alert_item": top_item,
            "source_query_states": source_query_states,
            "match_type": "Not assessed - alert expired",
            "match_reason": "Alert end time has passed; monitor only, no new plan issued.",
            "event_family": weather_event_family(top_event),
            "category_hints": WEATHER_FAMILY_CATEGORY_HINTS.get(weather_event_family(top_event), []),
            "alert_lifecycle": alert_lifecycle,
        }

    # See can_arrive_before_deadline() for the named deadline model (W03).
    def _can_arrive_in_time(transit_hours: float) -> bool:
        return can_arrive_before_deadline(
            transit_hours,
            alert_is_active=alert_is_active,
            hours_until_ends_precise=hours_until_ends_precise,
            has_end_time=has_end_time,
            hours_until_event_precise=hours_until_event_precise,
            has_onset_time=has_onset_time,
        )

    scenario_rows: List[Dict[str, Any]] = []
    staffing_rows: List[Dict[str, Any]] = []
    employee_details: List[Dict[str, Any]] = []

    ledger = weather_result.get("_atp_ledger")
    if ledger is None:
        ledger = {}
    lanes = FixtureRepository("v1").table("store_sku_dc_lanes")
    for _, store in affected_stores.sort_values("Store ID").iterrows():
        assumptions = weather_scenario_assumption_lookup(top_event, str(store["Region"]), history)
        if not assumptions["sufficient_evidence"]:
            return {"status": "insufficient_evidence", "reason": "One or more affected regions lack sufficient scenario assumptions; no combined demand plan issued.", "scenario_df": pd.DataFrame(), "state": state, "event": top_event, "affected_stores": affected_stores.to_dict("records"), "geography_match": geography_match.to_dict()}
        store_id = store["Store ID"]
        staffing = evaluate_staffing_exposure(
            store,
            area_desc_raw,
            state,
            employee_schedule,
            hours_until_event=hours_until_event,
            alert_is_active=alert_is_active,
            hours_until_ends=hours_until_ends,
        )
        staffing_rows.append(
            {
                "Store ID": store_id,
                "Store Name": store["Store Name"],
                "Scheduled Staff": staffing["Scheduled Staff"],
                "Scheduled Labor Hours": staffing["Scheduled Labor Hours"],
                "At-risk Commute Staff": staffing["At-risk Commute Staff"],
                "At-risk Labor Hours": staffing["At-risk Labor Hours"],
                "Expected Available Before Alert": staffing["Expected Available Before Alert"],
                "Expected Available Labor Hours": staffing["Expected Available Labor Hours"],
                "Expected Operating Capacity %": staffing["Expected Operating Capacity %"],
                "On-call Backup Staff": staffing["On-call Backup Staff"],
                "Commute Risk %": staffing["Commute Risk %"],
                "Staffing Risk": staffing["Staffing Risk"],
                "Staffing Action": staffing["Staffing Action"],
            }
        )
        for emp in staffing.get("employee_detail", []):
            employee_details.append({"Store ID": store_id, "Store Name": store["Store Name"], **emp})

        store_items = store_inventory[store_inventory["Store ID"] == store_id]
        for category, uplift_pct in assumptions["by_category"].items():
            category_items = store_items[store_items["Category"] == category]
            for _, inv_row in category_items.iterrows():
                upc = str(inv_row["UPC"])
                product = str(inv_row["Product"])
                baseline = float(inv_row["Baseline Forecast"])
                on_hand = float(inv_row["On Hand"])
                inbound = float(inv_row["Inbound"])
                store_inventory_as_of = str(inv_row.get("Captured At", ""))
                projected_demand_raw, planning_demand = compute_planning_demand(baseline, uplift_pct)
                available = on_hand + inbound
                gap = max(0, planning_demand - available)
                case_pack = int(inv_row.get("Case Pack", 1) or 1)
                moq = int(inv_row.get("MOQ", 0) or 0)
                order = calculate_order_quantity(gap, case_pack=case_pack, moq=moq)
                order_quantity = order.order_quantity

                route_row = route_network[route_network["Store ID"] == store_id]
                product_lanes = lanes[(lanes["Store ID"].astype(str) == str(store_id)) & (lanes["UPC"].astype(str) == upc) & lanes["Active"].astype(bool)]
                # Primary DC is a Store x Product decision, not a blanket Store-level one:
                # resolve it from this Store+UPC's own approved lane (Service Level ==
                # "primary") so a SKU deliberately sourced from a different DC than the
                # store's default is honored. The store's "Nearby DC ID" is only a
                # fallback for when no product-specific lane row exists at all.
                primary_lanes_for_product = product_lanes[product_lanes["Service Level"] == "primary"]
                if not primary_lanes_for_product.empty:
                    primary_dc_id = str(primary_lanes_for_product.iloc[0]["DC ID"])
                else:
                    primary_dc_id = str(store["Nearby DC ID"])
                primary_dc_row = dc_network[dc_network["DC ID"] == primary_dc_id]
                primary_dc_name = primary_dc_row.iloc[0]["DC Name"] if not primary_dc_row.empty else "Unassigned DC"
                primary_dc_county = primary_dc_row.iloc[0]["County"] if not primary_dc_row.empty else ""
                primary_inv_row = dc_inventory[
                    (dc_inventory["DC ID"] == primary_dc_id)
                    & (dc_inventory["UPC"] == upc)
                ]
                # Use "Available to Push" (emergency-spare stock for one affected store),
                # not the DC's full "On Hand" total that already covers its normal
                # replenishment obligations to every other store it serves -- otherwise
                # the primary DC always looks able to cover any single store's gap and
                # the alternate DC allocation below never actually gets exercised.
                primary_dc_on_hand = (
                    float(primary_inv_row.iloc[0]["Available to Push"])
                    if not primary_inv_row.empty
                    else 0.0
                )
                primary_dc_on_hand = max(0.0, primary_dc_on_hand - ledger.get((primary_dc_id, upc), 0.0))
                primary_lane = product_lanes[product_lanes["DC ID"] == primary_dc_id]
                primary_dc_in_transit = (
                    float(primary_inv_row.iloc[0]["In Transit"])
                    if not primary_inv_row.empty
                    else 0.0
                )

                primary_transit = (
                    float(route_row.iloc[0]["Transit Hours"])
                    if not route_row.empty
                    else float("inf")
                )
                if not primary_lane.empty:
                    primary_transit = float(primary_lane.iloc[0]["Transit Hours"])

                # Route/road-disruption exposure for the Store's normal (primary) lane,
                # computed once here so both the Primary DC's eligibility and the
                # candidate alternates below can weigh it consistently.
                route_exposure_flag = bool(route_row.iloc[0]["Route Weather Exposure"]) if not route_row.empty else False
                # W05: check every county the lane's own "Route Counties" fixture field
                # lists (origin, destination, and any intermediate county the lane
                # actually passes through), not just the Store's and primary DC's own
                # county -- a route can cross a county that intersects the alert even
                # when neither endpoint does.
                route_counties_raw = str(route_row.iloc[0].get("Route Counties", "")) if not route_row.empty else ""
                route_county_tokens = [
                    token.replace(" County", "").strip().lower()
                    for token in route_counties_raw.split(";")
                    if token.strip()
                ] or [
                    str(store["County"]).replace(" County", "").lower(),
                    str(primary_dc_county).replace(" County", "").lower(),
                ]
                route_crosses_alert = any(
                    token and token in area_desc for token in route_county_tokens
                ) or route_exposure_flag
                # Two different questions, kept as two separate values (W05): whether the
                # route geographically crosses the alert is a plain fact, independent of
                # whether there is currently anything to ship over it. "Route Risk" (the
                # existing column/Priority-escalation signal) stays defined the same way
                # it always has -- gated on an actual shortfall -- so Priority/urgency
                # logic elsewhere is unaffected. "Route Exposed" is the new, ungated fact,
                # so a well-stocked Store's route exposure is no longer invisible.
                route_exposed = route_crosses_alert
                route_action_required = gap > 0 and route_exposed
                route_risk = route_action_required

                # Select the best alternate DC for this Store/product rather than
                # blindly using one hard-coded backup. This is still a demonstration
                # proxy: actual production logic should use real GIS/route distance.
                alternate_candidates = []
                candidate_dcs = dc_network[
                    dc_network["DC ID"].astype(str) != primary_dc_id
                ].copy()

                for _, candidate in candidate_dcs.iterrows():
                    candidate_id = str(candidate["DC ID"])
                    candidate_lane = product_lanes[product_lanes["DC ID"] == candidate_id]
                    if candidate_lane.empty:
                        continue
                    candidate_inv = dc_inventory[
                        (dc_inventory["DC ID"] == candidate_id)
                        & (dc_inventory["UPC"] == upc)
                    ]
                    candidate_on_hand = (
                        float(candidate_inv.iloc[0]["Available to Push"])
                        if not candidate_inv.empty
                        else 0.0
                    )
                    candidate_on_hand = max(0.0, candidate_on_hand - ledger.get((candidate_id, upc), 0.0))
                    candidate_captured_at = (
                        str(candidate_inv.iloc[0].get("Captured At", ""))
                        if not candidate_inv.empty
                        else ""
                    )

                    same_region = str(candidate.get("Region", "")) == str(store.get("Region", ""))
                    same_state = str(candidate.get("State", "")) == str(store.get("State", ""))
                    candidate_transit = float(candidate_lane.iloc[0]["Transit Hours"])
                    candidate_county = str(candidate.get("County", ""))
                    candidate_county_under_alert = (
                        bool(candidate_county)
                        and candidate_county.replace(" County", "").lower()
                        in area_desc
                    )
                    # DC selection must weigh road/route disruption, not just the raw
                    # transit hours: a candidate whose route runs through the alert
                    # geography gets a demonstration disruption penalty added to its
                    # travel time before the ship-by deadline is checked, so an exposed
                    # "nearest" DC is not preferred over a clear-route alternative purely
                    # on distance. The unpenalized Transit Hrs is still what is displayed.
                    effective_candidate_transit = (
                        candidate_transit + ROUTE_DISRUPTION_PENALTY_HOURS
                        if candidate_county_under_alert
                        else candidate_transit
                    )
                    # Eligibility now actually checks the alert start time / latest
                    # ship-by time (via _can_arrive_in_time), not just "a lane exists."
                    candidate_can_arrive = _can_arrive_in_time(effective_candidate_transit)

                    alternate_candidates.append(
                        {
                            "DC ID": candidate_id,
                            "DC Name": str(candidate.get("DC Name", candidate_id)),
                            "County": candidate_county,
                            "On Hand": candidate_on_hand,
                            "Transit Hrs": candidate_transit,
                            "Effective Transit Hrs": effective_candidate_transit,
                            "Route Exposed": candidate_county_under_alert,
                            "Can Arrive": candidate_can_arrive,
                            "Same Region": same_region,
                            "Same State": same_state,
                            "Captured At": candidate_captured_at,
                        }
                    )

                # Same route-disruption treatment for the Primary DC's own lane, and the
                # eligibility check now uses the real ship-by deadline (alert start, or
                # alert end if the alert is already active) instead of only requiring a
                # lane to exist with a finite transit value.
                effective_primary_transit = (
                    primary_transit + ROUTE_DISRUPTION_PENALTY_HOURS if route_crosses_alert else primary_transit
                )
                primary_can_arrive = bool(not primary_lane.empty) and _can_arrive_in_time(effective_primary_transit)
                allocation = allocate_product_requirement(
                    store_id=str(store_id),
                    sku=upc,
                    required_quantity=order_quantity,
                    primary={
                        "dc_id": primary_dc_id,
                        "dc_name": primary_dc_name,
                        "atp": primary_dc_on_hand,
                        "eta_hours": effective_primary_transit,
                        "eligible": primary_can_arrive,
                    },
                    alternates=[
                        {
                            "dc_id": candidate["DC ID"],
                            "dc_name": candidate["DC Name"],
                            "atp": candidate["On Hand"],
                            "eta_hours": candidate["Effective Transit Hrs"],
                            "eligible": candidate["Can Arrive"],
                        }
                        for candidate in alternate_candidates
                    ],
                )
                ledger[(primary_dc_id, upc)] = ledger.get((primary_dc_id, upc), 0.0) + allocation.primary_supply
                if allocation.backup_supply:
                    ledger[(allocation.backup_dc_id, upc)] = ledger.get((allocation.backup_dc_id, upc), 0.0) + allocation.backup_supply
                primary_alloc = allocation.primary_supply
                backup_alloc = allocation.backup_supply
                remaining = allocation.remaining_gap
                backup_dc_id = allocation.backup_dc_id
                backup_dc_name = allocation.backup_dc_name
                backup_dc_on_hand = allocation.backup_atp
                selected_backup = next(
                    (
                        candidate
                        for candidate in alternate_candidates
                        if candidate["DC ID"] == backup_dc_id
                    ),
                    {},
                )
                backup_transit = float(selected_backup.get("Transit Hrs", 0.0) or 0.0)
                backup_can_arrive = bool(selected_backup.get("Can Arrive", False))
                backup_inventory_as_of = str(selected_backup.get("Captured At", ""))
                primary_inventory_as_of = (
                    str(primary_inv_row.iloc[0].get("Captured At", ""))
                    if not primary_inv_row.empty
                    else ""
                )

                # OIC-1: Backup DC identity, eligibility status, and the reason it isn't
                # eligible are three separate questions and now three separate fields --
                # "Backup DC" always stays a clean, filterable name (or the plain
                # "No eligible alternate" placeholder when no alternate exists at all),
                # never a name with an eligibility annotation smashed into it. When no
                # alternate could beat the deadline, name the nearest one anyway (with its
                # available stock and transit) instead of a bare "No eligible alternate"
                # -- a planner needs to see that a backup DC does exist and exactly why it
                # wasn't used -- but that "why" belongs in "Backup DC Reason", and the
                # "Ineligible" fact belongs in "Backup DC Status", not in the name itself.
                # backup_dc_id empty means allocate_product_requirement either found no
                # eligible candidate at all, or (per its own comment) deliberately didn't
                # consult one because the primary DC alone covered the requirement --
                # either way, "Backup DC" is the "No eligible alternate" placeholder and
                # there is no real backup to call Eligible, regardless of whether
                # alternate_candidates (the raw, not-yet-deadline-checked candidate list)
                # happens to be non-empty.
                #
                # A THIRD distinct case: allocate_product_requirement keeps naming the
                # nearest eligible candidate even when neither it nor the primary DC can
                # cover the requirement alone (its own "no split shipment, escalate
                # residual gap" branch) -- backup_dc_id is set, but backup_alloc is 0 and
                # nothing actually shipped from it. Calling that "Eligible" contradicted
                # "Backup Planned Qty: 0" and an escalated Residual Gap right next to it.
                if backup_dc_id and backup_alloc > 0:
                    backup_dc_status = "Eligible"
                elif backup_dc_id:
                    backup_dc_status = "Insufficient"
                else:
                    backup_dc_status = "No alternate"
                backup_dc_reason = (
                    "Has stock but not enough alone to cover the full requirement; no split shipment per policy."
                    if backup_dc_status == "Insufficient"
                    else ""
                )
                # Only substitute a display name when NO alternate was eligible at all
                # (backup_dc_id empty). When an alternate WAS eligible but only had
                # partial stock, it already has its own real name/ATP from the
                # allocation and must keep them -- overwriting it here would mislabel
                # a genuinely timing-eligible DC as ineligible just because its transit
                # happens to differ from the nearest candidate's.
                if backup_alloc == 0 and remaining > 0 and not backup_dc_id and alternate_candidates:
                    nearest = min(alternate_candidates, key=lambda c: c["Transit Hrs"])
                    backup_dc_name = nearest["DC Name"]
                    backup_dc_status = "Ineligible"
                    backup_dc_reason = (
                        f"Nearest alternate has {nearest['On Hand']:.0f} units available, but its "
                        f"{nearest['Transit Hrs']:.1f}h transit cannot beat the alert deadline, so it is not used."
                    )
                    backup_dc_on_hand = nearest["On Hand"]
                    backup_transit = nearest["Transit Hrs"]
                    backup_inventory_as_of = str(nearest.get("Captured At", ""))

                if gap <= 0:
                    supply_plan = f"No emergency replenishment required; {primary_dc_name} remains the normal serving DC."
                else:
                    plan_parts = []
                    if primary_alloc > 0:
                        timing_text = "before the alert window" if hours_until_event > 0 else "as soon as conditions allow"
                        plan_parts.append(f"advance-ship {primary_alloc:.0f} units from {primary_dc_name} {timing_text}")
                    if backup_alloc > 0:
                        plan_parts.append(f"route {backup_alloc:.0f} units from {backup_dc_name} (approx. {backup_transit:.1f}h demo transit)")
                    if remaining > 0:
                        plan_parts.append(f"escalate remaining {remaining:.0f}-unit shortfall for additional DC/supplier coverage")
                    supply_plan = "; ".join(plan_parts) if plan_parts else "Replenishment path requires manual review before the alert window."
                    supply_plan += f" Allocation rule: {allocation.allocation_reason}"
                    if backup_dc_reason:
                        supply_plan += f" {backup_dc_reason}"

                scenario_rows.append(
                    {
                        "Store ID": store_id,
                        "Store Name": store["Store Name"],
                        "County": store["County"],
                        "Match Precision": match_precision,
                        "Event": top_event,
                        "UPC": upc,
                        "Product": product,
                        "Category": category,
                        "Baseline Forecast": baseline,
                        "Scenario Demand Assumption %": uplift_pct,
                        "Projected Demand": planning_demand,
                        "Projected Demand Raw": round(projected_demand_raw, 2),
                        "Demand Rounding Rule": "Planning Demand = ceil(Baseline x (1 + Uplift %)); never rounded down.",
                        "On Hand": on_hand,
                        "Inbound": inbound,
                        "Store Inventory As Of": store_inventory_as_of,
                        "Available (On Hand + Inbound)": available,
                        "Inventory Gap": gap,
                        "Forecast Shortfall": gap,
                        "Order Quantity": order_quantity,
                        "Case Pack": case_pack,
                        "MOQ": moq,
                        "Quantity Rounding": order.rounding_reason,
                        "Stock Readiness": "Thin - push needed" if gap > 0 else "Ready",
                        "Primary DC": primary_dc_name,
                        "Primary DC Available ATP": primary_dc_on_hand,
                        "Primary DC In Transit": primary_dc_in_transit,
                        "Primary Inventory As Of": primary_inventory_as_of,
                        "Primary Transit Hrs": primary_transit,
                        "Primary Can Arrive By Deadline": "Yes" if primary_can_arrive else "No",
                        "Primary Planned Qty": primary_alloc,
                        "Availability Basis": "Synthetic captured fixture; no live inventory freshness certification",
                        "Allocation Deadline": "Alert end" if alert_is_active else "Alert start",
                        "Backup DC": backup_dc_name,
                        "Backup DC Status": backup_dc_status,
                        "Backup DC Reason": backup_dc_reason,
                        "Backup DC Available ATP": backup_dc_on_hand,
                        "Backup Transit Hrs": backup_transit,
                        "Backup Can Arrive By Deadline": "Yes" if backup_can_arrive else "No",
                        "Backup Inventory As Of": backup_inventory_as_of,
                        "Backup Planned Qty": backup_alloc,
                        "Backup Selection": allocation.allocation_reason,
                        "Allocation Reason": allocation.allocation_reason,
                        "Residual Gap": remaining,
                        "Hours Until Alert": hours_until_event,
                        "Alert Status": "Active now" if alert_is_active else "Upcoming",
                        "Hours Until Alert Ends": hours_until_ends if has_end_time else None,
                        "Route Crosses Alert Area": "Yes" if route_crosses_alert else "No",
                        # W05: pure geographic fact, never gated on whether there is
                        # currently a shortfall to ship -- a well-stocked Store's route
                        # exposure must still be visible, distinct from whether action is
                        # required right now ("Route Risk", unchanged, gap-gated below).
                        "Route Exposed": "Yes" if route_exposed else "No",
                        "Route Risk": "Yes" if route_risk else "No",
                        "Replenishment Plan": supply_plan,
                        "Demand Formula": "Projected Demand (Planning) = ceil(Baseline Forecast × (1 + Scenario Demand Assumption %)); Projected Demand Raw is the unrounded statistical forecast.",
                        "ATP Formula": "ATP = On Hand - Committed - Safety Stock + Eligible Inbound Before Cutoff",
                        "Internal Dataset Version": "fixtures-v1",
                    }
                )

    # groupby("Phase") returns alphabetical order, so the timeline read
    # During-Event, Post-Event, Pre-Event -- a sequence that starts in the middle.
    # Review requirement (Traffic assumptions): state plainly what the number is (a
    # percentage change vs. normal traffic, not a raw multiplier) and also give the
    # equivalent "Nx normal" reading so a multiplier is never mistaken for a percentage,
    # per the review's example (1.1x = 10% above normal, 0.9x = 10% below normal).
    # Review requirement (Traffic assumptions), extended: state which kind of "traffic"
    # this is (customer store visits/footfall -- not road/commute traffic, which is a
    # separate concept covered by the DC route-exposure and employee-commute figures
    # elsewhere), and explain in plain business terms why the pattern is up, then down,
    # then up again: customers rush to stock up before a severe-weather event, fewer
    # customers travel or shop while it is actively underway, then traffic rebounds as
    # customers return to restock and replace damaged goods afterward.
    traffic_rows = [
        {
            "Phase": phase,
            "Customer Store Traffic %": delta,
            "Traffic vs Normal (multiplier)": f"{1 + delta / 100.0:.2f}x",
            "What This Means": traffic_phase_explanation(phase, delta),
        }
        for phase, delta in sorted(
            assumptions.get("traffic_by_phase", {}).items(),
            key=lambda item: WEATHER_PHASE_ORDER.get(item[0], 99),
        )
    ]

    return {
        "status": "ok",
        "scenario_df": pd.DataFrame(scenario_rows),
        "staffing_df": pd.DataFrame(staffing_rows),
        "employee_detail_df": pd.DataFrame(employee_details),
        "traffic_df": pd.DataFrame(traffic_rows),
        "state": state,
        "region": region,
        "event": top_event,
        "match_precision": match_precision,
        "affected_stores": affected_stores[["Store ID", "Store Name", "County"]].to_dict("records"),
        "closed_stores_in_footprint": closed_stores_in_footprint,
        "comparable_events": assumptions["comparable_events"],
        "evidence_code": geography_match.evidence_code,
        "geography_match": geography_match.to_dict(),
        "by_phase": assumptions["by_phase"],
        "traffic_by_phase": assumptions.get("traffic_by_phase", {}),
        "demand_evidence_mode": "scenario_assumption" if assumptions.get("match_type") == "Exact Scenario Match" else "analog_scenario_assumption",
        "demand_evidence_label": "Scenario Demand Assumption" if assumptions.get("match_type") == "Exact Scenario Match" else "Analog Scenario Demand Assumption",
        "assumption_version": "scenario-assumptions-v1",
        "match_type": assumptions.get("match_type", "Exact Scenario Match"),
        "match_reason": assumptions.get("match_reason", "Sufficient scenario evidence found."),
        "event_family": assumptions.get("event_family", weather_event_family(top_event)),
        "matched_event_types": assumptions.get("matched_event_types", [top_event]),
        "category_hints": assumptions.get("category_hints", []),
        "hours_until_event": hours_until_event,
        "hours_until_ends": hours_until_ends if has_end_time else None,
        "alert_is_active": alert_is_active,
        "alert_lifecycle": alert_lifecycle,
        "duration_hours": duration_hours,
        "alert_item": top_item,
    }
