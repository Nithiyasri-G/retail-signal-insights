from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
import math
import pandas as pd

from market_intelligence.config.settings import WEATHER_COMMUTE_AVERAGE_SPEED_MPH
from market_intelligence.data.demo import demo_employee_schedule, demo_staffing_summary, demo_zip_centroids


def staffing_callout_rate(store_id: str, staffing_summary: Optional[pd.DataFrame] = None) -> float:
    """Recent callout rate for a store, from aggregated staffing data (0.0-1.0)."""
    staffing_summary = staffing_summary if staffing_summary is not None else demo_staffing_summary()
    store_rows = staffing_summary[staffing_summary["Store ID"] == store_id]
    if store_rows.empty or store_rows["Scheduled"].sum() == 0:
        return 0.0
    return round(float(store_rows["Callouts"].sum()) / float(store_rows["Scheduled"].sum()), 2)


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two lat/long points."""
    radius_miles = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius_miles * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def evaluate_staffing_exposure(
    store: pd.Series,
    area_desc: str,
    state: str,
    employee_schedule: Optional[pd.DataFrame] = None,
    *,
    hours_until_event: float = 0.0,
    alert_is_active: bool = False,
    hours_until_ends: float = 0.0,
    zip_centroids: Optional[Dict[str, Tuple[float, float]]] = None,
) -> Dict[str, Any]:
    """Assess staffing from current scheduled employees and commute geography.

    Historical callout/closure values are not used to classify the current staffing risk.
    The decision is based on the current schedule, an employee-ZIP-to-Store-ZIP commute
    distance/time estimate, the alert's own timing, and available on-call backup capacity,
    matching Ajith's review feedback. When an employee's ZIP has no centroid on file, the
    assessment falls back to the coarser home-county-vs-alert-area proxy rather than
    guessing a distance, and says so explicitly in that employee's row.
    """
    employee_schedule = employee_schedule if employee_schedule is not None else demo_employee_schedule()
    zip_centroids = zip_centroids if zip_centroids is not None else demo_zip_centroids()
    store_latitude = store.get("Latitude")
    store_longitude = store.get("Longitude")
    has_store_coordinates = store_latitude is not None and store_longitude is not None and not pd.isna(store_latitude) and not pd.isna(store_longitude)
    # The time an employee actually has to reach the store before conditions worsen:
    # time until the alert starts, or -- if it is already active -- time until it ends.
    # Unknown timing (neither available) is treated as no time budget, so an unresolved
    # commute is flagged rather than silently assumed safe.
    if alert_is_active:
        time_budget_hours = hours_until_ends if hours_until_ends > 0 else None
    else:
        time_budget_hours = hours_until_event if hours_until_event > 0 else None
    store_rows = employee_schedule[employee_schedule["Store ID"] == store["Store ID"]].copy()
    scheduled = store_rows[store_rows["Scheduled For Alert Window"] == True].copy()  # noqa: E712
    backups = store_rows[store_rows["On-call Eligible"] == True].copy()  # noqa: E712

    if scheduled.empty:
        return {
            "Scheduled Staff": 0,
            "Scheduled Labor Hours": 0.0,
            "At-risk Commute Staff": 0,
            "At-risk Labor Hours": 0.0,
            "Expected Available Before Alert": 0,
            "Expected Available Labor Hours": 0.0,
            "On-call Backup Staff": int(len(backups)),
            "Commute Risk %": 0.0,
            "Staffing Risk": "Unknown",
            "Staffing Action": "Confirm the current schedule before the alert window.",
            "employee_detail": [],
        }

    store_county = str(store.get("County", ""))
    county_token = store_county.replace(" County", "").lower()
    county_precise = bool(county_token) and county_token in str(area_desc or "").lower()

    def _assess_commute(emp: pd.Series) -> Tuple[bool, str, Optional[float], Optional[float]]:
        """Estimate whether this employee can reach the store before conditions worsen.

        Uses the employee's home ZIP centroid and the Store's own lat/long to estimate
        commute distance and time (at a fixed demonstration average speed), then compares
        that time against however long remains before the alert starts (or ends, if it is
        already active). Falls back to the coarser home-county-vs-alert-area proxy only
        when a ZIP centroid or Store coordinate is unavailable.
        """
        zip_code = str(emp.get("Home ZIP", "")).strip()
        centroid = zip_centroids.get(zip_code)
        if centroid and has_store_coordinates:
            distance_miles = _haversine_miles(
                float(store_latitude), float(store_longitude), centroid[0], centroid[1]
            )
            commute_hours = distance_miles / WEATHER_COMMUTE_AVERAGE_SPEED_MPH
            if time_budget_hours is None:
                return (
                    True,
                    f"~{distance_miles:.0f} mi / {commute_hours:.1f}h estimated commute; "
                    "alert timing unavailable, confirm arrival before conditions worsen.",
                    distance_miles,
                    commute_hours,
                )
            at_risk = commute_hours > time_budget_hours
            outcome = (
                "may not arrive before conditions worsen" if at_risk
                else "estimated to arrive before conditions worsen"
            )
            return (
                at_risk,
                f"~{distance_miles:.0f} mi / {commute_hours:.1f}h estimated commute from home ZIP {zip_code}; {outcome}.",
                distance_miles,
                commute_hours,
            )
        # No ZIP centroid on file (or no Store coordinates) -- fall back to the coarser
        # county-level proxy rather than inventing a distance.
        if county_precise:
            if str(emp.get("Home County", "")) == store_county:
                return (False, "ZIP centroid unavailable; home county matches the affected store county.", None, None)
            return (True, "ZIP centroid unavailable; commutes from outside the affected store county.", None, None)
        return (True, "ZIP centroid unavailable and alert scope is broad; commute confirmation required.", None, None)

    detail: List[Dict[str, Any]] = []
    at_risk = 0
    at_risk_hours = 0.0
    scheduled_hours = float(pd.to_numeric(scheduled["Scheduled Hours"], errors="coerce").fillna(0).sum())

    for _, emp in scheduled.iterrows():
        commute_at_risk, commute_status, distance_miles, commute_hours = _assess_commute(emp)

        hours = float(emp.get("Scheduled Hours", 0) or 0)
        if commute_at_risk:
            at_risk += 1
            at_risk_hours += hours
        detail.append(
            {
                "Employee Token": emp["Employee Token"],
                "Schedule Status": "Scheduled",
                "Shift": emp["Shift"],
                "Scheduled Hours": hours,
                "Home ZIP": emp["Home ZIP"],
                "Home County": emp["Home County"],
                "Commute Distance (mi)": round(distance_miles, 1) if distance_miles is not None else None,
                "Commute Time (hrs)": round(commute_hours, 1) if commute_hours is not None else None,
                "Commute Assessment": commute_status,
            }
        )

    # Surface the on-call pool separately so Store Operations knows what backup capacity
    # exists if scheduled staff cannot reach the store.
    for _, emp in backups.iterrows():
        detail.append(
            {
                "Employee Token": emp["Employee Token"],
                "Schedule Status": "On-call backup",
                "Shift": emp["Shift"],
                "Scheduled Hours": 0.0,
                "Home ZIP": emp["Home ZIP"],
                "Home County": emp["Home County"],
                "Commute Distance (mi)": None,
                "Commute Time (hrs)": None,
                "Commute Assessment": "Backup candidate - confirm availability before alert",
            }
        )

    scheduled_count = int(len(scheduled))
    risk_pct = round(at_risk / scheduled_count, 2) if scheduled_count else 0.0
    available_before = max(0, scheduled_count - at_risk)
    available_hours = max(0.0, scheduled_hours - at_risk_hours)
    backup_count = int(len(backups))

    if risk_pct >= 0.60:
        staffing_risk = "High"
    elif risk_pct >= 0.40:
        staffing_risk = "Elevated"
    else:
        staffing_risk = "Low"

    if staffing_risk == "High":
        action = (
            f"{at_risk} of {scheduled_count} scheduled staff ({risk_pct * 100:.0f}%) have commute exposure. "
            f"Expected lower-risk scheduled capacity before the alert is {available_before} staff / {available_hours:.0f} labor hours. "
            f"Confirm {backup_count} on-call backup staff, ask lower-risk employees to arrive before conditions deteriorate, "
            "and consider adjusted store hours with advance customer communication."
        )
    elif staffing_risk == "Elevated":
        action = (
            f"{at_risk} of {scheduled_count} scheduled staff have commute exposure. Confirm {backup_count} on-call backup staff "
            "and earlier arrival for lower-risk employees before the alert window."
        )
    else:
        action = (
            f"Current scheduled staffing appears manageable ({at_risk} of {scheduled_count} commute-at-risk). "
            f"Keep {backup_count} on-call staff available and reconfirm commute conditions before the alert window."
        )

    operating_capacity_pct = round(
        (available_before / scheduled_count) * 100.0, 1
    ) if scheduled_count else 0.0

    return {
        "Scheduled Staff": scheduled_count,
        "Scheduled Labor Hours": round(scheduled_hours, 1),
        "At-risk Commute Staff": at_risk,
        "At-risk Labor Hours": round(at_risk_hours, 1),
        "Expected Available Before Alert": available_before,
        "Expected Available Labor Hours": round(available_hours, 1),
        "Expected Operating Capacity %": operating_capacity_pct,
        "On-call Backup Staff": backup_count,
        "Commute Risk %": risk_pct,
        "Staffing Risk": staffing_risk,
        "Staffing Action": action,
        "employee_detail": detail,
    }
