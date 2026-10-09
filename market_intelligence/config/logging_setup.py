from logging.handlers import RotatingFileHandler
from typing import Any
import logging
import os

from market_intelligence.config.settings import RUN_HISTORY_DB


# Structured, file-based operational log. The Streamlit UI's status messages are
# ephemeral -- they vanish when the session ends or reruns -- so a run/source/incident/
# decision failure has no retrievable trail without this. Co-located with the run
# history DB by default; never surfaced in the UI itself (diagnostic only).
_operational_logger = logging.getLogger("market_intelligence.operations")


if not _operational_logger.handlers:
    _operational_logger.setLevel(logging.INFO)
    _stream_handler = logging.StreamHandler()
    _stream_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _operational_logger.addHandler(_stream_handler)
    try:
        _log_path = os.path.join(os.path.dirname(os.path.abspath(RUN_HISTORY_DB)) or ".", "market_intelligence_operations.log")
        _file_handler = RotatingFileHandler(_log_path, maxBytes=5_000_000, backupCount=3)
        _file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        _operational_logger.addHandler(_file_handler)
    except OSError:
        pass  # e.g. read-only filesystem -- stderr logging above still works
    _operational_logger.propagate = False


def log_operational_event(event: str, level: str = "info", **fields: Any) -> None:
    """Log one structured operational event (run/source/incident/decision lifecycle).

    Never raises and never surfaces in the Streamlit UI -- logging must not be able to
    break the app it is trying to make diagnosable.
    """
    try:
        payload = " ".join(f"{key}={value}" for key, value in fields.items())
        message = f"event={event} {payload}".strip()
        getattr(_operational_logger, level, _operational_logger.info)(message)
    except Exception:
        pass
