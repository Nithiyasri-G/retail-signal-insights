from typing import List
import os

# Project root (the folder holding app.py, .env and data/): settings.py lives at
# <root>/market_intelligence/config/settings.py, so three levels up.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional local convenience
    load_dotenv = None


APP_DIRECTORY = PROJECT_ROOT


ENV_FILE = os.path.join(APP_DIRECTORY, ".env")


if load_dotenv:
    # Resolve the .env sitting beside app.py rather than relative to whatever directory
    # the app was launched from, so `streamlit run /path/to/app.py` picks the keys up
    # from anywhere. A real environment variable always wins over the file, which keeps
    # a deployed environment's configuration authoritative.
    load_dotenv(ENV_FILE, override=False)
    load_dotenv(override=False)


def credentials_from_env() -> List[str]:
    """Name the credentials that came from the environment, never their values.

    Without this the field simply renders blank when the .env was not picked up, and
    there is no way to tell "no key" from "key not loaded".
    """
    return [
        label
        for label, variable in (
            ("LLM", "NVIDIA_API_KEY"),
            ("Apify", "APIFY_API_TOKEN"),
            ("BLS", "BLS_API_KEY"),
        )
        if str(os.getenv(variable, "") or "").strip()
    ]


NVIDIA_CHAT_URL = os.getenv("NVIDIA_CHAT_URL", "https://integrate.api.nvidia.com/v1/chat/completions").strip()


DEFAULT_NVIDIA_MODEL = "nvidia/nemotron-3-super-120b-a12b"


NVIDIA_MAX_FEATURE_ROWS = 8


NVIDIA_MAX_ARTICLES = 4


NVIDIA_MAX_TOKENS = 520


NVIDIA_TIMEOUT_SECONDS = [35]


NVIDIA_SIGNAL_ANALYSIS_MAX_TOKENS = 180


NVIDIA_FINAL_SUMMARY_MAX_TOKENS = 420


BLS_CPI_SERIES = {
    "Headline CPI": "CUUR0000SA0",
    "Food at home": "CUUR0000SAF11",
    "Household furnishings": "CUUR0000SAH3",
    "Gasoline": "CUUR0000SETB",
}


APIFY_ALLOWED_TIME_RANGES = ["", "now 1-H", "now 4-H", "now 1-d", "now 7-d", "today 1-m", "today 3-m", "today 5-y", "all"]


APIFY_SAFE_TIME_RANGE = "now 7-d"


APIFY_HARD_KEYWORD_LIMIT = 2


# Demonstration road-disruption penalty (hours) added to a DC lane's transit time when
# that lane's route runs through the alert-affected geography, before checking whether
# it can still arrive by the alert's ship-by deadline. Production logic should replace
# this with real route/GIS disruption estimates.
ROUTE_DISRUPTION_PENALTY_HOURS = 3.0


# Demonstration average ground-travel speed (mph) used to convert an employee's
# ZIP-centroid-to-Store distance into an estimated commute time under weather-alert
# conditions. Production logic should replace this with a real routing/traffic API.
WEATHER_COMMUTE_AVERAGE_SPEED_MPH = 25.0


# Local run history for lightweight versioning on the office/local machine.
# Tests and managed deployments can place persistence outside the source tree.
RUN_HISTORY_DB = os.getenv(
    "MARKET_INTELLIGENCE_DB",
    os.path.join(PROJECT_ROOT, "market_intelligence_runs.db"),
)
