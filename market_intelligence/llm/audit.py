from typing import Any
from typing import Dict
from typing import List
import os
import pandas as pd

from market_intelligence.config.settings import NVIDIA_MAX_ARTICLES, NVIDIA_MAX_FEATURE_ROWS, NVIDIA_TIMEOUT_SECONDS
from market_intelligence.llm.prompts import build_llm_payload, llm_system_prompt, llm_user_prompt
from market_intelligence.util.hashing import payload_hash
from market_intelligence.util.text import display_fallback_reason  # noqa: F401  (re-exported)


def show_llm_trace_detail() -> bool:
    """Prompt text and raw payloads are internal review material, not client material."""
    return os.getenv("SHOW_LLM_TRACE", "false").strip().lower() in {"1", "true", "yes", "on"}


def build_base_llm_audit(
    feature_df: pd.DataFrame,
    articles: List[Dict[str, Any]],
    retailer: str,
    region: str,
    model: str,
) -> Dict[str, Any]:
    payload = build_llm_payload(feature_df, articles)
    return {
        "provider": "LLM agent",
        "model": model,
        "sent_to_llm": False,
        "brief_source": "not_generated",
        "fallback_used": False,
        "fallback_reason": "",
        "mock_data_used": False,
        "feature_rows_available": int(len(feature_df)),
        "feature_rows_sent": 0,
        "articles_available": int(len(articles)),
        "articles_sent": 0,
        "payload_hash_sha256": payload_hash(payload),
        "system_prompt": llm_system_prompt(),
        "user_prompt": llm_user_prompt(retailer, region, payload),
        "payload": payload,
        "analysis_contract": "The brief must be grounded only in the collected feature rows and article records shown in this audit view.",
        "brief_generation_strategy": f"Payload capped at {NVIDIA_MAX_FEATURE_ROWS} feature rows and {NVIDIA_MAX_ARTICLES} articles; agent retries use timeouts {NVIDIA_TIMEOUT_SECONDS} seconds with lightweight backoff.",
    }


def exportable_llm_audit(llm_audit: Dict[str, Any]) -> Dict[str, Any]:
    """Strip prompt text and raw payloads from the downloadable evidence bundle.

    The Brief Trace tab hides these behind SHOW_LLM_TRACE, but a download that ignores
    that flag would ship the whole prompt to anyone who clicked it. The audit claim --
    what was sent, how much, and its fingerprint -- survives without the prompt text.
    """
    if not isinstance(llm_audit, dict):
        return {}
    internal = {"system_prompt", "user_prompt", "payload", "actual_request"}
    if show_llm_trace_detail():
        return dict(llm_audit)
    exported = {key: value for key, value in llm_audit.items() if key not in internal}
    exported["prompt_and_payload"] = (
        "Withheld from the export. The payload fingerprint and row counts above identify "
        "exactly what was sent; enable SHOW_LLM_TRACE for the full text."
    )
    # A01: each retry attempt in nvidia_attempts carries its OWN actual_request (with its
    # own system_prompt/user_prompt/payload) and raw_response -- stripping only the
    # top-level keys left the full prompt text sitting untouched inside every nested
    # attempt, defeating the redaction this function exists to do.
    attempts = exported.get("nvidia_attempts")
    if isinstance(attempts, list):
        redacted_attempts = []
        for attempt in attempts:
            if not isinstance(attempt, dict):
                redacted_attempts.append(attempt)
                continue
            redacted_attempt = {key: value for key, value in attempt.items() if key not in internal}
            if "raw_response" in redacted_attempt:
                redacted_attempt["raw_response"] = "Withheld from the export. Enable SHOW_LLM_TRACE for the full text."
            redacted_attempts.append(redacted_attempt)
        exported["nvidia_attempts"] = redacted_attempts
    return exported
