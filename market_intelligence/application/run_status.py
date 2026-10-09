from typing import Any
from typing import Dict

from market_intelligence.llm.audit import display_fallback_reason


def brief_pipeline_state(brief_source: str, llm_audit: Dict[str, Any]) -> Dict[str, str]:
    """Report the brief step honestly in the pipeline strip.

    This step was hard-coded to "success" regardless of outcome, so a green SUCCESS
    badge sat directly above "Fallback brief generated: ... failed semantic validation"
    on every tab. A local brief is a working result, not a success of the AI step, so it
    is reported as "skipped" -- the neutral state this strip already uses for a step that
    did not run externally.
    """
    if str(brief_source or "").lower() == "nvidia":
        return {"status": "success", "detail": "Briefing Agent wrote the brief"}
    reason = display_fallback_reason(llm_audit.get("fallback_reason") or "")
    return {
        "status": "skipped",
        "detail": f"Rule-based brief used. {reason}".strip(),
    }


def run_progress_pct(states: Dict[str, Dict[str, str]]) -> int:
    """Completion across the steps that were meant to run.

    The bar was hard-coded to 100% at the end of a run, so it read "100%" beside a step
    badged SKIPPED -- a contradiction on the first thing anyone looks at.
    """
    attempted = [
        state for state in states.values()
        if str(state.get("status", "")).lower() not in {"queued", "disabled"}
    ]
    if not attempted:
        return 0
    finished = [
        state for state in attempted
        if str(state.get("status", "")).lower() in {"success", "failed", "skipped"}
    ]
    succeeded = [state for state in attempted if str(state.get("status", "")).lower() == "success"]
    if len(finished) < len(attempted):
        return int(round((len(finished) / len(attempted)) * 100))
    return int(round((len(succeeded) / len(attempted)) * 100))
