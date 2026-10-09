from typing import Tuple

from market_intelligence.config.settings import DEFAULT_NVIDIA_MODEL

from market_intelligence.infra import nvidia as nvidia_client

def friendly_nvidia_failure(raw_error: str) -> Tuple[str, str]:
    error_text = str(raw_error or "").strip()
    lower = error_text.lower()
    if "empty" in lower or "no message content" in lower:
        return (
            "rule-based (agent unavailable)",
            "The agent did not return usable brief content after retry, so a rule-based summary was written from the collected signal rows.",
        )
    if "timed out" in lower or "read timeout" in lower:
        return (
            "rule-based (agent timeout)",
            "The agent did not respond within the configured timeout, so a rule-based brief was written from the collected signal rows.",
        )
    if "401" in lower or "unauthorized" in lower:
        return (
            "rule-based (key rejected)",
            "The LLM API key was rejected, so a rule-based brief was written from the collected signal rows.",
        )
    if "429" in lower or "rate limit" in lower:
        return (
            "rule-based (rate limit)",
            "Rate limiting prevented the agent from writing the brief, so a rule-based brief was written from the collected signal rows.",
        )
    return (
        "rule-based (agent unavailable)",
        "The agent was unavailable for this run, so a rule-based summary was used instead.",
    )


def test_nvidia_minimal(api_key: str, model: str) -> Tuple[bool, str]:
    if not api_key:
        return False, "LLM API key not provided."
    ok, text, error, elapsed = nvidia_client._nvidia_chat_request(api_key, model or DEFAULT_NVIDIA_MODEL,
        "Return only the requested short answer.", "Reply with: Connected", 1400, 60)
    return ok, f"{model or DEFAULT_NVIDIA_MODEL}: {elapsed} ms; {text[:100] if ok else error}"


def validate_nvidia(api_key: str, model: str) -> Tuple[bool, str]:
    return test_nvidia_minimal(api_key, model)


def friendly_apify_failure(raw_error: str) -> str:
    """Translate a raw Apify exception into an actionable message.

    Apify was previously disabled here after an account credit issue; a raw
    apify-client exception (a generic HTTP/SDK error string) doesn't make it obvious
    whether that's happening again, versus a bad token, a rate limit, or something
    else entirely. This mirrors the existing friendly_nvidia_failure pattern so the
    same "diagnose from the message" experience exists for Apify.
    """
    error_text = str(raw_error or "").strip()
    lower = error_text.lower()
    if any(token in lower for token in ["insufficient", "not enough", "usage limit", "monthly usage", "out of credit", "exceeded your", "402"]):
        return (
            "Apify account appears to be out of credits or over its usage limit "
            f"(this is the credit issue Apify was previously disabled for). Raw error: {error_text}"
        )
    if "401" in lower or "unauthorized" in lower or "invalid token" in lower or "authentication" in lower:
        return f"Apify token was rejected (invalid, expired, or revoked) -- generate a new token in the Apify Console. Raw error: {error_text}"
    if "429" in lower or "rate limit" in lower or "too many requests" in lower:
        return f"Apify rate-limited this request -- wait a bit before trying again. Raw error: {error_text}"
    if "timed out" in lower or "timeout" in lower:
        return f"The Apify actor run timed out -- this can happen on shared/free compute; try again or check the Apify Console run logs. Raw error: {error_text}"
    if "not found" in lower or "404" in lower:
        return f"The Apify actor 'apify/google-trends-scraper' could not be reached -- confirm it is still published and available to your account. Raw error: {error_text}"
    return f"Apify request failed -- check the Apify Console run logs for detail. Raw error: {error_text}"


def validate_apify(token: str) -> Tuple[bool, str]:
    if not token:
        return False, "Apify token not provided. Apify collectors will be skipped."
    try:
        from apify_client import ApifyClient
    except ImportError:
        return False, "apify-client is not installed. Run: pip install apify-client (add it to requirements.txt so it's installed wherever this app is deployed)."
    try:
        client = ApifyClient(token)
        user = client.user().get()
        username = user.get("username") or user.get("email") or "Apify user"
        return True, f"Connected as {username}."
    except Exception as exc:  # pragma: no cover - depends on live Apify service
        return False, friendly_apify_failure(str(exc))
