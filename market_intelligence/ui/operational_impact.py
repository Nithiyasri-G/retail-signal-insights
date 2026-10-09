from __future__ import annotations


def operational_status_line(
    weather_status: str,
    recall_status: str,
    historical_count: int,
) -> str:
    """Explain configured source mode separately from the current run state."""
    statuses = [
        str(value or "Not run").replace("_", " ").title().replace("Not Run", "Not run")
        for value in (weather_status, recall_status)
    ]
    current_status = " / ".join(dict.fromkeys(statuses))
    if current_status == "Success":
        current_status = "Success"
    return (
        "Configured sources: Live NOAA/openFDA | "
        f"Current run: {current_status} | Historical incidents available: {int(historical_count)}"
    )


def normalize_missing_evidence(value: str) -> str:
    """Keep legacy persisted wording honest when displayed in the new UI."""
    text = str(value or "").strip()
    return (
        text.replace("Precise NOAA geography → Store match", "NOAA geography → Store match evidence")
        .replace("County (precise)", "County name fallback")
    )


def history_selector_label(row: dict[str, object]) -> str:
    """Create a compact, distinguishable label for a historical incident."""
    return " | ".join(
        str(row.get(key, "") or "")
        for key in ("Incident ID", "Event", "State", "Created At")
    )


def operational_number_formats(columns: list[str] | tuple[str, ...]) -> dict[str, str]:
    """Return readable formats for numeric operational tables without changing values.

    The display names matter as much as the source names: several of these tables rename
    a column on the way to the screen ("Primary DC Available ATP" -> "Primary ATP",
    "Baseline Forecast" -> "Baseline"), and a name missing from these sets renders as a
    raw float -- which is why cells were showing 157.000000.
    """
    # Hours and percentage/assumption values are genuinely continuous, so one decimal
    # place is meaningful for them.
    one_decimal = {
        "Hours Until Alert",
        "Hours Until Alert Ends",
        "Primary Transit Hrs",
        "Backup Transit Hrs",
        "Scheduled Labor Hours",
        "At-risk Labor Hours",
        "Expected Available Labor Hours",
        "Scenario Demand Assumption %",
        "Uplift %",
        "Customer Store Traffic %",
        "Expected Operating Capacity %",
        "Commute Distance (mi)",
        "Commute Time (hrs)",
        # The unrounded statistical forecast -- legitimately continuous, unlike the
        # whole-unit Planning Demand ("Projected Demand") it is derived from.
        "Projected Demand Raw",
    }
    # Everything below counts physical, sellable/shippable units -- a fractional value
    # like "132.9 bottles" or "157.4 units of ATP" is not explainable in business terms,
    # so these are always whole numbers.
    whole_units = {
        "Order Quantity",
        "Case Pack",
        "MOQ",
        "Units per Case",
        "MOQ (units)",
        "Forecast Shortfall",
        "Projected Demand",
        "Baseline Forecast",
        "Baseline",
        "Historical Average",
        "On Hand",
        "Substitute On Hand",
        "In Transit",
        "Inbound",
        "Available (On Hand + Inbound)",
        "Available Supply",
        "Inventory Gap",
        "Gap",
        "Primary DC Available ATP",
        "Primary ATP",
        "Primary DC In Transit",
        "Backup DC Available ATP",
        "Backup ATP",
        "Residual Gap",
        "Remaining Gap",
        "Primary Planned Qty",
        "Backup Planned Qty",
        "Available Qty",
        "Planned Qty",
        "Scheduled Staff",
        "At-risk Commute Staff",
        "Expected Available Before Alert",
        "On-call Backup Staff",
        "Units Sold",
        "Store Count",
    }
    return {
        column: "{:,.1f}" if column in one_decimal else "{:,.0f}"
        for column in columns
        if column in one_decimal or column in whole_units
    }


def weather_inbox_columns() -> tuple[str, ...]:
    """Client-facing Weather inbox columns, in the order a planner reads them.

    Identity first (which alert is this), then exposure (does it reach us), then the
    decision (what now), then the evidence behind the demand figure.

    Two changes matter for how this table is read. "Exposure Method" and "Store Match
    Type" answered two different questions under names that both sounded like geography,
    so a county-name text match could be reported as a "Direct Historical Match"; they
    are now "Store Match" (geography) and "Demand Basis" (demand evidence). And
    "Incident Summary" is gone: it concatenated four adjacent columns and, being the
    widest, pushed the scannable ones off the right edge. It is still carried in the
    CSV export and the incident selector.
    """
    return (
        # Identity -- which alert is this.
        "Incident Reference",
        "Weather Event",
        "Area of Impact",
        "Alert Window",
        # Exposure -- does it reach us, and how precisely.
        "Store Exposure",
        "Store Match",
        # Decision -- what now, and where it stands.
        "Operational Priority",
        "Actionability",
        "Recommended Action",
        "Action Status",
        # Evidence -- read last, after the alert is known to reach us at all.
        "Demand Basis",
        "Evidence Strength",
    )


def replenishment_columns() -> tuple[str, ...]:
    """Columns for the DC Replenishment Plan, trimmed so the alternate DC is on screen.

    The per-product alternate DC is the point of this table -- it is what answers "which
    DC actually covers this item" -- but with eighteen columns it sat behind a horizontal
    scrollbar, so the table read as one DC serving everything. Case pack, MOQ and the
    inventory as-of stamps are still shown in the Store Demand table above. The free-text
    "Replenishment Plan" column was removed from here: it duplicated these same numbers as
    prose and, because the duplication was independently derived, could drift out of sync
    with them (see Backup DC Status's "Eligible"-vs-"Insufficient" fix). The AI-suggested
    plan below this table is grounded directly in these columns instead.
    """
    return (
        "Store ID",
        "Store Name",
        "Product",
        "Forecast Shortfall",
        "Order Quantity",
        "Primary DC",
        "Primary DC Available ATP",
        "Primary Planned Qty",
        "Backup DC",
        "Backup DC Status",
        "Backup DC Available ATP",
        "Backup Planned Qty",
        "Residual Gap",
        "Route Risk",
    )
