import re
from typing import Any


def display_fallback_reason(reason: Any) -> str:
    """Turn an internal diagnostic into a sentence a business reader can act on.

    The raw strings behind these are things like "semantic validation failed: template
    or meta language detected". Those belong in the run audit, not on a results page
    where the only useful information is that the local grounded summary was used.
    """
    text = str(reason or "").strip()
    lower = text.lower()
    if not text:
        return "The agent's response was used."
    if "empty nvidia response" in lower or "nvidia returned an empty response" in lower or "empty message content" in lower:
        return "The agent returned no usable content, so a rule-based summary was used instead."
    if "semantic validation" in lower or "template slot" in lower or "meta language" in lower or "missing headings" in lower:
        return "The agent did not follow the required report structure, so a rule-based summary was used instead."
    if "timeout" in lower or "timed out" in lower:
        return "The agent did not respond in time, so a rule-based summary was used instead."
    if "authentication" in lower or "rate limit" in lower:
        return "The agent was unavailable, so a rule-based summary was used instead."
    return _plain_wording(text)


_LEGACY_WORDING = (
    ("AI service", "The agent"),
    ("configured NVIDIA chat endpoint", "configured agent service"),
    ("NVIDIA account/service status", "account status"),
    ("NVIDIA", "The agent"),
)


def _plain_wording(text: str) -> str:
    """Runs saved before the wording change still carry vendor and \"AI\" phrases; show them in the
    current vocabulary and drop the raw endpoint URL, which is internal detail."""
    cleaned = re.sub(r"\s*Endpoint:\s*\S+", "", text)
    for old, new in _LEGACY_WORDING:
        cleaned = cleaned.replace(old, new)
    return cleaned
