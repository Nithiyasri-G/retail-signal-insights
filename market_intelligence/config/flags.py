import os


def show_admin_controls() -> bool:
    """Whether to show the developer-facing Raw Data tab.

    The Credentials panel is always shown now (it used to be hidden by this same
    HIDE_ADMIN_CONTROLS switch, which meant the key field could vanish on one machine
    and not another for reasons nothing on screen explained). This switch still governs
    the separate Raw Data tab, which is developer material, not something to show a
    client.
    """
    return os.getenv("HIDE_ADMIN_CONTROLS", "false").strip().lower() not in {"1", "true", "yes", "on"}


def scenario_lab_enabled() -> bool:
    """Whether the "What-if scenario" tab is offered inside the Operational Impact Center.

    Scenarios are synthetic and isolated from live incidents; switch the tab off with
    ENABLE_SCENARIO_LAB=false in a session where only live operational data may be shown.
    """
    return os.getenv("ENABLE_SCENARIO_LAB", "true").strip().lower() in {"1", "true", "yes", "on"}


def signal_search_enabled() -> bool:
    """Whether the Signal Search tab (a paid Apify Google Trends lookup) and the Apify token field are offered.

    Off by default: the lookup needs an Apify account. Set ENABLE_SIGNAL_SEARCH=true where one is available.
    """
    return os.getenv("ENABLE_SIGNAL_SEARCH", "false").strip().lower() in {"1", "true", "yes", "on"}
