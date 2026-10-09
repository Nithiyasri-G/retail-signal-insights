from decimal import Decimal
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple
import json
import pandas as pd
import re

from market_intelligence.config.settings import DEFAULT_NVIDIA_MODEL
from market_intelligence.llm.validation import validate_generated_report
from market_intelligence.weather.wording import ROUTE_RISK_WARNING

from market_intelligence.infra import nvidia as nvidia_client

def local_incident_explanation(incident: Dict[str, Any]) -> str:
    lines = [
        "INCIDENT SUMMARY",
        f"{incident['Type']} incident {incident['Incident ID']}: {incident['Affected Scope']}.",
        "",
        "CALCULATED IMPACT",
    ]
    if incident["Impact Metrics"]:
        lines.extend(f"- {k}: {v}" for k, v in incident["Impact Metrics"].items())
    else:
        lines.append("- No quantified impact -- see limitations below.")
    lines += ["", "RECOMMENDED ACTIONS"]
    if incident["Recommended Actions"]:
        lines.extend(f"- {a['Action']} ({a['Urgency']}, owner: {a['Owner']}): {a['Reason']}" for a in incident["Recommended Actions"])
    else:
        lines.append("- No action recommended at this time.")
    lines += ["", "LIMITATIONS"]
    lines.extend(f"- {limitation}" for limitation in incident["Limitations"])
    return "\n".join(lines)


def generate_incident_explanation(api_key: str, model: str, incident: Dict[str, Any]) -> Tuple[str, str]:
    """NVIDIA explains already-calculated incident facts; it never decides matches or impact."""
    fallback = local_incident_explanation(incident)
    if not api_key:
        return fallback, "Local summary"
    payload = {
        "incident_id": incident["Incident ID"],
        "type": incident["Type"],
        "affected_scope": incident["Affected Scope"],
        "impact_metrics": incident["Impact Metrics"],
        "recommended_actions": incident["Recommended Actions"],
        "limitations": incident["Limitations"],
    }
    system_prompt = (
        "You are a retail operations analyst. Explain only the supplied incident facts in plain business language. "
        "Do not invent numbers, do not decide matches, do not mark any action as completed."
    )
    user_prompt = (
        f"Explain this operational incident for a store operations planner:\n{json.dumps(payload, indent=2, default=str)}\n\n"
        "Return: INCIDENT SUMMARY, CALCULATED IMPACT, RECOMMENDED ACTIONS, LIMITATIONS. Use only the supplied numbers."
    )
    ok, content, error, _ = nvidia_client._nvidia_chat_request(
        api_key=api_key,
        model=model or DEFAULT_NVIDIA_MODEL,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=420,
        timeout_seconds=35,
    )
    if not ok:
        return fallback, "Local summary"
    valid, _ = validate_generated_report(content, ("INCIDENT SUMMARY", "CALCULATED IMPACT", "RECOMMENDED ACTIONS", "LIMITATIONS"))
    return (content, "AI summary") if valid else (fallback, "Local summary")


_DC_REPLENISHMENT_FACT_COLUMNS: Tuple[str, ...] = (
    "Product",
    "Order Quantity",
    "Primary DC",
    "Primary DC Available ATP",
    "Primary Planned Qty",
    "Backup DC",
    "Backup DC Status",
    "Backup DC Available ATP",
    "Backup Planned Qty",
    "Residual Gap",
    "Route Risk",
)


def local_dc_replenishment_recommendation(gap_rows: pd.DataFrame, store_name: str) -> str:
    """Deterministic fallback recommendation, built directly from the DC Replenishment
    Plan table's own columns -- never from independently-derived prose. This is what
    renders whenever NVIDIA is unavailable or its answer fails grounding validation; this
    app never leaves a recommendation blank.
    """
    if gap_rows.empty:
        return f"No open inventory gap at {store_name} for the filtered products; no replenishment action needed."
    lines = []
    # Route risk belongs to the store's delivery lane, not to one product row: when any row for
    # the store is flagged, every replenishment instruction for that store carries the warning.
    store_route_risk = "Route Risk" in gap_rows.columns and bool((gap_rows["Route Risk"].astype(str) == "Yes").any())
    for _, row in gap_rows.iterrows():
        parts = [f"{row.get('Product', 'Unknown product')}: order quantity {float(row.get('Order Quantity', 0) or 0):.0f} units."]
        primary_qty = float(row.get("Primary Planned Qty", 0) or 0)
        if primary_qty > 0:
            parts.append(f"{primary_qty:.0f} from {row.get('Primary DC', 'primary DC')}.")
        backup_qty = float(row.get("Backup Planned Qty", 0) or 0)
        if backup_qty > 0:
            parts.append(f"{backup_qty:.0f} from {row.get('Backup DC', 'backup DC')} ({row.get('Backup DC Status', '')}).")
        residual = float(row.get("Residual Gap", 0) or 0)
        if residual > 0:
            parts.append(f"{residual:.0f} unit(s) remain unresolved -- escalate for additional DC/supplier coverage.")
        if store_route_risk:
            parts.append(f"{ROUTE_RISK_WARNING}.")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _recommendation_numbers_are_grounded(content: str, facts: List[Dict[str, Any]]) -> bool:
    """Reject a generated recommendation that states a number absent from the supplied
    facts -- the same discipline used for the Executive Brief validator (L01/L03): a
    plausible-sounding number is still wrong if it does not trace back to real evidence.
    """
    supplied = json.dumps(facts, default=str)
    allowed = {
        Decimal(value.replace(",", ""))
        for value in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", supplied)
    }
    for value in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", content):
        if Decimal(value) not in allowed:
            return False
    return True


def generate_dc_replenishment_recommendation(
    api_key: str, model: str, store_name: str, gap_rows: pd.DataFrame
) -> Tuple[str, str]:
    """AI-suggested DC replenishment plan for one store, grounded strictly in the DC
    Replenishment Plan table's own rows for that store.

    Replaces the table's former free-text "Replenishment Plan" column and the per-store
    supply-chain action card: both restated the same Primary/Backup DC numbers as
    independently-derived prose, which could (and did) drift out of sync with the table
    itself -- see the Backup DC Status "Eligible"-vs-"Insufficient" fix. This sends
    exactly the table's own rows as evidence, instructs the model to name only what is
    in them, and rejects (falling back to a deterministic summary) any response that
    states a number not present in that evidence.
    """
    fallback = local_dc_replenishment_recommendation(gap_rows, store_name)
    if gap_rows.empty:
        return fallback, "No gap"
    if not api_key:
        return fallback, "Local summary"
    facts = gap_rows[[c for c in _DC_REPLENISHMENT_FACT_COLUMNS if c in gap_rows.columns]].to_dict("records")
    system_prompt = (
        "You are a retail supply-chain planner. Recommend a replenishment plan using ONLY "
        "the supplied facts. Never invent a quantity, DC name, or status not present in the "
        "evidence. If a product's gap cannot be covered by the supplied Primary/Backup "
        "allocation, say so plainly and recommend escalation rather than inventing a source "
        "for it. Do not use markdown headings."
    )
    user_prompt = (
        f"Store: {store_name}\nProduct-level DC allocation facts (one entry per affected product):\n"
        f"{json.dumps(facts, indent=2, default=str)}\n\n"
        "Write a concise (3-6 sentence) recommended replenishment plan for this store's planner, "
        "naming only the DCs, quantities and statuses that appear above."
    )
    ok, content, error, _ = nvidia_client._nvidia_chat_request(
        api_key=api_key,
        model=model or DEFAULT_NVIDIA_MODEL,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=320,
        timeout_seconds=30,
    )
    if not ok or not str(content or "").strip():
        return fallback, "Local summary"
    if not _recommendation_numbers_are_grounded(content, facts):
        return fallback, "Local summary"
    return content.strip(), "AI summary"
