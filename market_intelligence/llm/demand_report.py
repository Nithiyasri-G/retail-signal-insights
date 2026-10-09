from typing import Any
from typing import Dict
from typing import Tuple
import json
import re

from market_intelligence.demand.planning import AVAILABLE_SUPPLY_ASSUMPTION, GAP_DEFINITION, HISTORICAL_REFERENCE_NOTE, SURPLUS_DEFINITION
from market_intelligence.config.settings import DEFAULT_NVIDIA_MODEL
from market_intelligence.infra.credentials import friendly_nvidia_failure
from market_intelligence.llm.brief import _normalize_executive_brief_format
from market_intelligence.llm.validation import PLANNER_REPORT_SECTIONS, validate_generated_report

from market_intelligence.infra import nvidia as nvidia_client

def local_demand_planner_report(plan: Dict[str, Any]) -> str:
    signal = plan.get("Signal Area", "External signal")
    level = plan.get("Signal Level", "")
    direction = plan.get("Demand Direction", "REVIEW")
    baseline = plan.get("Baseline Forecast", 0)
    historical = plan.get("Historical Average", 0)
    supply = plan.get("Available Supply", 0)
    gap = plan.get("Baseline Gap", 0)
    surplus = plan.get("Baseline Surplus", 0)
    in_scope = plan.get("Products In Scope", plan.get("Reference Products", 0))
    in_store = plan.get("Products In Store", in_scope)
    category_scope = plan.get("Category Scope", "")
    scope_basis = plan.get("Scope Basis", "")
    scoped = str(plan.get("Planning Scope", "")).lower().startswith("categories")

    if scoped:
        scope_text = (
            f"{in_scope} of {in_store} products at this store are in scope ({category_scope}). "
            f"{scope_basis}"
        )
    else:
        scope_text = (
            f"All {in_store} products at this store are in scope. {scope_basis}"
        )

    store_id = plan.get("Selected Store ID", "")
    store_name = str(plan.get("Selected Store Name", "") or "")
    store_text = ""
    if store_id:
        store_text = f"The selected planning context is {store_name.replace(' - ', ' in ', 1) if store_name else 'Store ' + str(store_id)}. "

    if gap > 0:
        inventory_text = f"Current baseline supply gap: {gap} units across the products in scope (available supply {supply} units). {GAP_DEFINITION}"
        if surplus:
            inventory_text += " " + SURPLUS_DEFINITION.format(surplus=surplus)
    else:
        inventory_text = (
            f"No current baseline supply gap across the products in scope (available supply {supply} units). "
            + (SURPLUS_DEFINITION.format(surplus=surplus) if surplus else "")
        ).strip()
    inventory_text += " " + AVAILABLE_SUPPLY_ASSUMPTION

    area = str(signal or "").upper()
    if "WEATHER" in area:
        action = (
            "The signal indicates a potential demand surge or replenishment disruption. Before changing any forecast, first map the weather event to the affected geography, stores/DCs, and relevant categories, then validate historical demand and current inventory."
        )
    elif "RECALL" in area:
        action = (
            "The signal indicates potential demand decline and substitution risk. Before changing any forecast, first match the recall to internal UPC/SKU data and identify the actually affected product, stores/DCs, inventory exposure, and substitutes."
        )
    elif "CPI" in area or "INFLATION" in area:
        action = (
            "The signal indicates a possible category mix or price-sensitivity shift. Validate it against internal category sales, basket mix, pricing, promotions, and inventory before changing any forecast."
        )
    elif "NEWS" in area:
        action = (
            "The news signal provides market context only. First determine whether the event is relevant to a specific category, geography, competitor set, or product before using it in a planning decision."
        )
    else:
        action = "Review the external signal with relevant internal demand, product, geography, and inventory evidence before making a planning change."

    return (
        "PLANNING SUMMARY\n"
        f"{signal} is a {level} external signal. The planning review direction is {direction}. "
        f"{store_text}{scope_text}\n\n"
        "FORECAST CONTEXT\n"
        f"The aggregated reference baseline forecast is {baseline} units and the Historical Sales Reference is {historical} units. {HISTORICAL_REFERENCE_NOTE} "
        "No manual uplift or decline percentage is applied. The external signal is used as planning context, not as an automatic forecast override.\n\n"
        "INVENTORY IMPACT\n"
        f"{inventory_text}\n\n"
        "PLANNING ACTION\n"
        f"{action}\n\n"
        "CONFIDENCE AND LIMITATIONS\n"
        "This view asks whether the current baseline forecast already anticipates the signal; it does not size an operational response to a specific alert, which is what the Operational Impact Center does. "
        "Forecast and inventory values are illustrative internal data. Signal-to-product mapping uses category-level rules; production would use the client's own product hierarchy, historical sales, promotions and forecast accuracy before any planning decision."
    )


_CONTRADICTION_PATTERNS = (
    r"store scope (?:includes|is)\s+\d+",
    r"\b(?:[2-9]|\d{2,})\s+stores\b",
    r"surplus\s*\(?[^.]{0,40}historical average",
    r"baseline forecast minus (?:the )?available supply",
    r"supply gap[^.]{0,40}(?:offset|netted) by (?:the )?surplus",
)


def report_contradicts_formulas(content: str, plan: Dict[str, Any]) -> bool:
    """True when the text restates a field in a way the deterministic plan rules out
    (Store ID read as a store count, surplus compared to historical average, gap described as
    total forecast minus total supply)."""
    text = str(content or "").lower()
    stores = int(plan.get("Stores Evaluated", 1) or 1)
    for pattern in _CONTRADICTION_PATTERNS:
        match = re.search(pattern, text)
        if not match:
            continue
        if pattern == r"\b(?:[2-9]|\d{2,})\s+stores\b":
            number = int(re.match(r"\d+", match.group(0)).group(0))
            if number == stores:
                continue
        return True
    return False


def generate_demand_planner_report(
    api_key: str,
    model: str,
    signal_row: Dict[str, Any],
    plan: Dict[str, Any],
) -> Tuple[str, str, Dict[str, Any]]:
    """Use NVIDIA only to explain the supplied signal and already-calculated planning facts."""
    fallback = local_demand_planner_report(plan)
    if not api_key:
        return fallback, "Local summary", {"fallback_reason": "No LLM API key provided."}

    payload = {
        "external_signal": signal_row,
        "calculated_planning_result": plan,
        "field_definitions": (
            "Store ID is an identifier, not a store count. Product-level surplus is measured against individual baseline "
            "forecasts, not historical average. Current Baseline Supply Gap is the sum of positive product shortages and may "
            "differ from total forecast minus total available supply. Historical Sales Reference is comparison only. "
            + AVAILABLE_SUPPLY_ASSUMPTION
        ),
        "calculation_contract": (
            "Forecast and inventory values were supplied or calculated before the LLM call. "
            "No manual demand uplift/decline factor is used. The LLM must explain the planning context and must not create new numeric values."
        ),
    }
    system_prompt = (
        "You are a retail demand planning analyst. Explain only the supplied external signal and deterministic planning facts. "
        "Do not invent new forecast, sales, inventory, uplift, substitution, or performance values."
    )
    user_prompt = ("Explain the supplied planning facts in plain text under these exact headings: "
        "PLANNING SUMMARY; FORECAST CONTEXT; INVENTORY IMPACT; PLANNING ACTION; CONFIDENCE AND LIMITATIONS. "
        "Put each heading on its own line, followed by substantive content. State that no automatic forecast override is applied. "
        "Explain that shortages are summed per product and other products' surpluses do not offset them. "
        "Use only the supplied numbers and distinguish synthetic planning inputs from external signals. "
        "No placeholders, reasoning or prompt discussion.\nEVIDENCE:\n" + json.dumps(payload, default=str))
    ok, content, error, elapsed_ms = nvidia_client._nvidia_chat_request(
        api_key=api_key,
        model=model or DEFAULT_NVIDIA_MODEL,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=380,
        timeout_seconds=40,
    )
    audit = {"elapsed_ms": elapsed_ms, "success": ok, "technical_error": error, "payload": payload}
    if not ok:
        audit["fallback_reason"] = friendly_nvidia_failure(error)[1]
        return fallback, "Local summary", audit

    # The same guard the Executive Brief uses. Without it this path printed whatever the
    # model returned, which is how the prompt itself -- "We need to produce the required
    # sections exactly as specified" and unfilled <...> slots -- reached the Planning
    # Report on screen and the planning_report.txt download.
    report = _normalize_executive_brief_format(content)
    if report_contradicts_formulas(report, plan):
        audit["success"] = False
        audit["technical_error"] = "failed validation: restates a planning field in a way the calculation rules out"
        audit["fallback_reason"] = "The model's answer contradicted the planning formulas, so the grounded local report was used instead."
        return fallback, "Local summary", audit
    report_valid, report_reason = validate_generated_report(report, PLANNER_REPORT_SECTIONS)
    if not report_valid:
        audit["success"] = False
        audit["technical_error"] = f"failed validation: {report_reason}"
        audit["fallback_reason"] = (
            "The model's answer did not follow the required report structure, so the "
            f"grounded local report was used instead ({report_reason})."
        )
        return fallback, "Local summary", audit
    return report, "AI summary", audit
