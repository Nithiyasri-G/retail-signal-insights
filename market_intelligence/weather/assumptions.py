from typing import Any
from typing import Dict
from typing import Optional
import pandas as pd

from market_intelligence.data.demo import scenario_weather_assumptions


WEATHER_EVENT_FAMILY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("flash flood", "flood", "heavy rain"), "Flood / Heavy Rain"),
    (("thunderstorm", "tornado", "wind", "hurricane", "tropical storm"), "Storm / Wind"),
    (("heat", "excessive heat"), "Heat"),
    (("winter", "freeze", "frost", "snow", "ice"), "Winter Weather"),
    (("air quality", "smoke", "ozone"), "Air Quality / Health"),
    (("rip current", "coastal", "surf", "beach"), "Coastal / Marine"),
    (("fog", "red flag", "fire weather"), "Visibility / Fire"),
)


WEATHER_FAMILY_CATEGORY_HINTS: dict[str, list[str]] = {
    "Flood / Heavy Rain": ["Emergency Essentials", "Food / Consumables", "Household"],
    "Storm / Wind": ["Emergency Essentials", "Household"],
    "Heat": ["Food / Consumables", "Personal Care"],
    "Winter Weather": ["Emergency Essentials", "Household", "Food / Consumables"],
    "Air Quality / Health": ["Personal Care", "Household"],
    "Coastal / Marine": ["Emergency Essentials", "Household"],
    "Visibility / Fire": ["Household", "Emergency Essentials"],
}


WEATHER_PHASE_ORDER: Dict[str, int] = {"Pre-Event": 0, "During-Event": 1, "Post-Event": 2}


# "County name fallback" is an internal branch name. In a headline it reads as something
# having failed, which invites the wrong question; the mapping says the same thing in the
# language the inbox already uses.
STORE_MATCH_PHRASES: Dict[str, str] = {
    "county name fallback": "matched on county name",
    "county (precise)": "matched on county",
    "noaa polygon": "inside the NOAA alert boundary",
    "polygon": "inside the NOAA alert boundary",
    "state only (broad scope)": "state-level only",
    "state only": "state-level only",
}


def store_match_phrase(precision: Any) -> str:
    text = str(precision or "").strip()
    return STORE_MATCH_PHRASES.get(text.lower(), text or "match method not recorded")


def weather_affected_scope(store_count: int, state: Any, precision: Any) -> str:
    """Readable affected-scope line: real pluralisation, no internal branch names."""
    noun = "Store" if store_count == 1 else "Stores"
    return f"{store_count} {noun} in {state} ({store_match_phrase(precision)})"


def weather_event_family(event_type: str) -> str:
    """Map granular NOAA event names to a business evidence family.

    This is intentionally a local POC rule, not a copied alert UI: it lets
    the app separate exact historical evidence from analog evidence without pretending
    that every live alert has the same past event type available.
    """
    text = str(event_type or "").strip().lower()
    for tokens, family in WEATHER_EVENT_FAMILY_RULES:
        if any(token in text for token in tokens):
            return family
    return "Other Weather"


def _weather_history_with_family(history: pd.DataFrame) -> pd.DataFrame:
    enriched = history.copy()
    if "Event Family" not in enriched.columns:
        enriched["Event Family"] = enriched["Event Type"].apply(weather_event_family)
    return enriched


def _weather_assumption_summary(matches: pd.DataFrame, *, sufficient: bool, match_type: str, event_family: str, matched_event_types: list[str], reason: str) -> Dict[str, Any]:
    comparable_events = int(matches["Event ID"].nunique()) if not matches.empty else 0
    if not sufficient or comparable_events < 3:
        return {
            "sufficient_evidence": False,
            "comparable_events": comparable_events,
            "by_category": {},
            "by_phase": {},
            "traffic_by_phase": {},
            "store_closure_rate": 0.0,
            "match_type": match_type,
            "evidence_ladder": match_type,
            "event_family": event_family,
            "matched_event_types": matched_event_types,
            "match_reason": reason,
            "category_hints": WEATHER_FAMILY_CATEGORY_HINTS.get(event_family, []),
        }

    by_category = matches[matches["Phase"] == "During-Event"].groupby("Category")["Demand Uplift %"].mean().round(1).to_dict()
    by_phase = matches.groupby("Phase")["Demand Uplift %"].mean().round(1).to_dict()
    traffic_by_phase = matches.groupby("Phase")["Traffic Delta %"].mean().round(1).to_dict() if "Traffic Delta %" in matches.columns else {}
    closure_events = matches[matches["Store Operating Status"] != "Open"]["Event ID"].nunique()
    return {
        "sufficient_evidence": True,
        "comparable_events": comparable_events,
        "by_category": by_category,
        "by_phase": by_phase,
        "traffic_by_phase": traffic_by_phase,
        "store_closure_rate": round(closure_events / comparable_events, 2),
        "match_type": match_type,
        "evidence_ladder": match_type,
        "event_family": event_family,
        "matched_event_types": matched_event_types,
        "match_reason": reason,
        "category_hints": WEATHER_FAMILY_CATEGORY_HINTS.get(event_family, []),
    }


def weather_scenario_assumption_lookup(event_type: str, region: str, history: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """Summarize exact or analog synthetic scenario cases for an alert type + region.

    The client-demo rule is an evidence ladder, not a forced exact-history lookup:
    Exact Scenario Match -> Analog Scenario Match -> Operational Exposure Match.  Quantitative
    demand assumptions are used only when exact or analog scenario evidence has at least
    three distinct comparable cases.  Exposure-only incidents remain actionable but do not
    invent uplift.
    """
    history = history if history is not None else scenario_weather_assumptions()
    history = _weather_history_with_family(history)
    event_text = str(event_type or "").strip()
    region_text = str(region or "")
    event_family = weather_event_family(event_text)

    exact_matches = history[
        (history["Event Type"].astype(str).str.lower() == event_text.lower())
        & (history["Region"].astype(str) == region_text)
    ]
    exact_count = int(exact_matches["Event ID"].nunique()) if not exact_matches.empty else 0
    if exact_count >= 3:
        return _weather_assumption_summary(
            exact_matches,
            sufficient=True,
            match_type="Exact Scenario Match",
            event_family=event_family,
            matched_event_types=[event_text],
            reason=f"{exact_count} exact scenario case(s) found for {event_text} in {region_text}.",
        )

    analog_matches = history[
        (history["Event Family"] == event_family)
        & (history["Region"].astype(str) == region_text)
    ]
    # Exclude the exact event rows here so the label remains honest when the exact count
    # is below the threshold but the broader family can still support a POC assumption.
    analog_matches = analog_matches[
        analog_matches["Event Type"].astype(str).str.lower() != event_text.lower()
    ]
    analog_count = int(analog_matches["Event ID"].nunique()) if not analog_matches.empty else 0
    if analog_count >= 3:
        matched_event_types = sorted(analog_matches["Event Type"].astype(str).unique().tolist())[:5]
        return _weather_assumption_summary(
            analog_matches,
            sufficient=True,
            match_type="Analog Scenario Match",
            event_family=event_family,
            matched_event_types=matched_event_types,
            reason=(
                f"Only {exact_count} exact case(s) found for {event_text}, but {analog_count} analog "
                f"case(s) exist in {event_family} for {region_text}."
            ),
        )

    return _weather_assumption_summary(
        exact_matches,
        sufficient=False,
        match_type="Operational Exposure Match",
        event_family=event_family,
        matched_event_types=[event_text] if event_text else [],
        reason=(
            f"Store/DC exposure can be evaluated, but only {exact_count} exact and {analog_count} analog "
            f"scenario case(s) are available for {event_text or 'this weather event'} in {region_text}."
        ),
    )
