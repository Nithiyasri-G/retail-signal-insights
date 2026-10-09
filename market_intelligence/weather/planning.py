from decimal import Decimal
from decimal import ROUND_CEILING
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
import re


def _traffic_pct_and_multiplier(delta_pct: Any) -> str:
    """Render a traffic/demand delta as an explicit percentage AND its 'Nx normal' reading.

    Review requirement (Traffic assumptions): state what the number represents and, if it
    is a multiplier, label it as such (e.g. 1.1x = 10% above normal) rather than leaving a
    bare figure that reads as ambiguous between a percentage and a multiplier.
    """
    value = float(delta_pct or 0)
    return f"{value:+.0f}% vs. normal ({1 + value / 100.0:.2f}x normal)"


def traffic_phase_explanation(phase: str, delta_pct: Any) -> str:
    """W07: the "What This Means" text was fixed per phase name regardless of the sign of
    that phase's own traffic delta, so a Pre-Event row showing -4% (fewer customers) could
    still read "rush-buying surge" (implying MORE customers) -- a direct contradiction
    between the number and the sentence next to it. The explanation must agree with the
    actual sign of the delta for that specific row.
    """
    value = float(delta_pct or 0)
    direction = "up" if value > 0 else "down" if value < 0 else "flat"
    phrases = {
        ("Pre-Event", "up"): "Customers stock up ahead of the event (rush-buying surge).",
        ("Pre-Event", "down"): "Customers delay or avoid trips ahead of the event.",
        ("Pre-Event", "flat"): "No material change in traffic ahead of the event.",
        ("During-Event", "up"): "More customers make essential, last-minute trips while the event is active.",
        ("During-Event", "down"): "Fewer customers travel or shop while the event is active.",
        ("During-Event", "flat"): "No material change in traffic while the event is active.",
        ("Post-Event", "up"): "Traffic rebounds as customers restock and replace damaged goods.",
        ("Post-Event", "down"): "Traffic stays below normal after the event.",
        ("Post-Event", "flat"): "No material change in traffic after the event.",
    }
    return phrases.get((phase, direction), "")


SAFETY_BUFFER_HOURS = 2.0  # Existing, already-approved handling buffer -- not invented here.


def can_arrive_before_deadline(
    transit_hours: float,
    *,
    alert_is_active: bool,
    hours_until_ends_precise: float,
    has_end_time: bool,
    hours_until_event_precise: float,
    has_onset_time: bool,
    safety_buffer_hours: float = SAFETY_BUFFER_HOURS,
) -> bool:
    """W03/route-deadline model, named explicitly rather than an inline comparison:

      assessment_time      = now (the caller's "now_ts")
      required_arrival_by  = alert onset (upcoming alert) or alert end (already-active
                              alert -- there is no "before it starts" window left, but
                              replenishment during an ongoing event is still useful)
      safety_buffer_hours  = 2.0, the existing approved handling buffer
      must_ship_by         = required_arrival_by - transit_hours - safety_buffer_hours

    Eligible iff assessment_time <= must_ship_by, i.e. transit_hours + safety_buffer_hours
    <= hours remaining until required_arrival_by. All inputs must be full floating-point
    precision (never pre-rounded) -- rounding before this comparison can flip the answer
    (6h58m rounds to 7.0h and would incorrectly pass a 7h-total-lead-time requirement).
    No usable onset/end timing at all is not treated as "zero time available" (which
    would trivially fail); it is simply ineligible, distinctly, via the has_*_time guards.
    """
    required_lead_hours = transit_hours + safety_buffer_hours
    if alert_is_active:
        return required_lead_hours <= hours_until_ends_precise if has_end_time else False
    if has_onset_time:
        return required_lead_hours <= hours_until_event_precise
    return False


def compute_planning_demand(baseline: float, uplift_pct: float) -> Tuple[float, int]:
    """Split a demand estimate into its raw statistical forecast and physical Planning Demand.

    A statistical forecast is legitimately continuous (e.g. "302.2 units expected"), but a
    physical order/shelf quantity is not -- there is no such thing as 0.2 of a bottle. This
    is the ONE boundary where the forecast becomes a physical planning quantity, and it
    applies an explicit ceiling, never a nearest-value round: rounding a demand estimate to
    the nearest whole unit would silently UNDER-order whenever the fractional part is below
    0.5 (a raw demand of 302.2 must plan for 303 units, not round down to 302 and leave 0.2
    units of demand unmet). The raw forecast is returned unchanged for audit/display and is
    never itself rounded again before being used elsewhere (e.g. for Inventory Gap).

    D01: the ceiling is computed with Decimal arithmetic, not plain IEEE-754 float
    multiplication. A float computation like 100.0 * (1 + 10/100.0) evaluates to
    110.00000000000001 due to binary floating-point representation error, which would make
    math.ceil() wrongly return 111 for a baseline that should plan for exactly 110 units.
    An arbitrary `round(raw, 6)` was deliberately rejected here: six decimal places is not
    a value this domain can justify as "guaranteed safe" for every baseline/uplift
    combination, whereas Decimal(str(x)) arithmetic has no representation error to begin
    with. `str(x)` is used (not `Decimal(x)` directly) because constructing a Decimal
    straight from a float would reintroduce that float's own binary imprecision.
    """
    baseline_dec = Decimal(str(baseline))
    uplift_dec = Decimal(str(uplift_pct))
    raw_dec = baseline_dec * (Decimal(1) + uplift_dec / Decimal(100))
    raw = float(raw_dec)
    planning = int(raw_dec.to_integral_value(rounding=ROUND_CEILING))
    return raw, planning


def _geometry_bounding_box(geometry: Optional[Dict[str, Any]]) -> Optional[Tuple[float, float, float, float]]:
    """(min_lon, max_lon, min_lat, max_lat) spanned by a NOAA alert's own polygon."""
    if not isinstance(geometry, dict):
        return None
    geometry_type = str(geometry.get("type") or "")
    coordinates = geometry.get("coordinates") or []
    polygons = [coordinates] if geometry_type == "Polygon" else coordinates if geometry_type == "MultiPolygon" else []
    lons: List[float] = []
    lats: List[float] = []
    for polygon in polygons:
        for ring in polygon:
            for point in ring:
                try:
                    lons.append(float(point[0]))
                    lats.append(float(point[1]))
                except (TypeError, ValueError, IndexError):
                    continue
    if not lons or not lats:
        return None
    return min(lons), max(lons), min(lats), max(lats)


def weather_alert_geocode_states(geocode: Optional[Dict[str, Any]]) -> set:
    """Extract the 2-letter state(s) a NOAA alert's own UGC/FIPS geocodes cover.

    A UGC code is stateCC### (e.g. "TXZ102", "GAC121"); the leading two letters are
    the state. Using this instead of the search-query state keeps "evaluated Stores"
    scoped to where the alert actually is, even for a multi-state or geometry-only alert.
    """
    states: set = set()
    if not isinstance(geocode, dict):
        return states
    ugc_values = geocode.get("UGC") or []
    if isinstance(ugc_values, str):
        ugc_values = [ugc_values]
    for value in ugc_values:
        match = re.match(r"^([A-Z]{2})[CZ]\d{3}", str(value or "").upper())
        if match:
            states.add(match.group(1))
    return states
