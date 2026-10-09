from typing import Any
from typing import Dict
from typing import Tuple
import time

from market_intelligence.config.settings import NVIDIA_CHAT_URL

from market_intelligence.infra import http as http_client

def nvidia_request_body(model: str, system_prompt: str, user_prompt: str, max_tokens: int) -> Dict[str, Any]:
    body = {"model": model, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "temperature": 0.2, "max_tokens": max(1400, max_tokens), "stream": False}
    if "nemotron-3-" in model:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    return body


def _nvidia_chat_request(api_key: str, model: str, system_prompt: str, user_prompt: str,
                         max_tokens: int, timeout_seconds: int) -> Tuple[bool, str, str, int]:
    started = time.monotonic()
    ok, data, msg = http_client.safe_request(NVIDIA_CHAT_URL, method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json_body=nvidia_request_body(model, system_prompt, user_prompt, max_tokens),
        timeout=timeout_seconds)
    elapsed = int((time.monotonic() - started) * 1000)
    if not ok:
        if "410" in str(msg) and "Gone" in str(msg):
            return False, "", (
                "HTTP 410 Gone from the configured agent service. The request did not reach "
                "brief validation. Check the service URL and account status; "
                "switching models on the same endpoint will not repair this response. "
                f"Endpoint: {NVIDIA_CHAT_URL}"
            ), elapsed
        return False, "", str(msg), elapsed
    choices = data.get("choices", []) if isinstance(data, dict) else []
    choice = choices[0] if choices else {}
    content = str(choice.get("message", {}).get("content") or "").strip()
    if choice.get("finish_reason") == "length":
        return False, content, "Response truncated at token limit; retry with larger output budget.", elapsed
    if not content:
        return False, "", "The agent returned no final answer content (reasoning-only or empty response).", elapsed
    return True, content, "", elapsed
