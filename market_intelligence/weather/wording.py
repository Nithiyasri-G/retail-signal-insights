"""Wording shared by every screen that tells a planner about route risk."""

ROUTE_RISK_WARNING = "Confirm a safe delivery lane before releasing the shipment"


def route_risk_clause() -> str:
    """The same warning, lower-cased for use inside a longer sentence."""
    return "weather-exposed route, so " + ROUTE_RISK_WARNING[0].lower() + ROUTE_RISK_WARNING[1:]
