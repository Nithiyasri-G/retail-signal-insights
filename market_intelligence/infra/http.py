import time
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple
from urllib.parse import urlparse

import requests

# Public data APIs (openFDA, NOAA, BLS) answer with an occasional 5xx or a dropped connection
# that clears within seconds. A read-only GET is safe to repeat, so it gets a few attempts
# before the collector reports the source as failed. POST requests (the paid NVIDIA call, BLS
# queries) are never repeated automatically.
GET_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.5
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_retryable(exc: requests.RequestException) -> bool:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError):
        return getattr(exc.response, "status_code", None) in RETRYABLE_STATUS_CODES
    return False


def _describe(exc: requests.RequestException, url: str) -> str:
    """A short message for the screen: the host and the status, not the whole query string."""
    host = urlparse(url).netloc or url
    if isinstance(exc, requests.HTTPError) and getattr(exc.response, "status_code", None):
        status = exc.response.status_code
        reason = "temporarily unavailable" if status >= 500 else "rejected the request"
        return f"{host} {reason} (HTTP {status})"
    if isinstance(exc, requests.Timeout):
        return f"{host} did not respond in time"
    if isinstance(exc, requests.ConnectionError):
        return f"Could not reach {host}"
    return str(exc)


def safe_request(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Tuple[bool, Any, str]:
    is_post = method.upper() == "POST"
    attempts = 1 if is_post else GET_ATTEMPTS
    message = ""
    for attempt in range(1, attempts + 1):
        try:
            if is_post:
                response = requests.post(url, headers=headers, params=params, json=json_body, timeout=timeout)
            else:
                response = requests.get(url, headers=headers, params=params, timeout=timeout)
            response.raise_for_status()
            try:
                return True, response.json(), "success"
            except ValueError:
                return True, response.text, "success"
        except requests.RequestException as exc:
            message = _describe(exc, url)
            if attempt < attempts and _is_retryable(exc):
                time.sleep(RETRY_DELAY_SECONDS * attempt)
                continue
            return False, None, message
    return False, None, message
