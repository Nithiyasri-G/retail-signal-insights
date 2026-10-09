from decimal import Decimal
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple
import json
import pandas as pd
import re


BRIEF_SECTIONS_FULL: Tuple[str, ...] = (
    "EXECUTIVE SUMMARY",
    "TOP INSIGHTS",
    "PLANNING RELEVANCE",
    "RECOMMENDED ACTIONS",
    "CONFIDENCE AND LIMITATIONS",
)


# The ultra-compact retry prompt deliberately drops PLANNING RELEVANCE to shrink the
# request. Validating every attempt against all five headings meant a correct retry was
# rejected for omitting a section it was never asked to write, and the run fell back to
# the local brief with "missing headings: PLANNING RELEVANCE".
BRIEF_SECTIONS_ULTRA_COMPACT: Tuple[str, ...] = (
    "EXECUTIVE SUMMARY",
    "TOP INSIGHTS",
    "RECOMMENDED ACTIONS",
    "CONFIDENCE AND LIMITATIONS",
)


# An unfilled template slot is a CLOSED pair of angle brackets around a short phrase --
# "<2 concise sentences>". Matching a bare "<" rejected ordinary prose such as
# "<5% growth" or "margin <1pt", which is how valid output was being discarded.
BRIEF_PLACEHOLDER_PATTERN = re.compile(r"<\s*[^<>\n]{2,80}\s*>")


# Meta language means the model talking about the task instead of doing it. The previous
# pattern included the bare phrase "we need to", which is ordinary business English
# ("we need to confirm sell-through"), so legitimate briefs were thrown away.
BRIEF_META_PATTERN = re.compile(
    r"\b(?:as an ai|i cannot|i can(?:'|’)t comply|the (?:user|prompt) (?:asked|wants|requested)|"
    r"we need to (?:produce|return|output|write|generate|follow)|per the instructions|"
    r"using only supplied payload|insert\s+\w+\s+here|your company name)\b",
    flags=re.IGNORECASE,
)


PLANNER_REPORT_SECTIONS: Tuple[str, ...] = (
    "PLANNING SUMMARY",
    "FORECAST CONTEXT",
    "INVENTORY IMPACT",
    "PLANNING ACTION",
    "CONFIDENCE AND LIMITATIONS",
)


def report_sections(text: str, headings: Tuple[str, ...]) -> Dict[str, str]:
    sections: Dict[str, list[str]] = {}
    current = None
    for line in str(text or "").splitlines():
        clean = re.sub(r"^[#*\s]+|[*\s:]+$", "", line).strip().upper()
        if clean in headings:
            current = clean
            sections.setdefault(current, [])
        elif current:
            sections[current].append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def validate_generated_report(text: str, required_sections: Tuple[str, ...]) -> Tuple[bool, str]:
    body = str(text or "").strip()
    if not body:
        return False, "empty response"
    sections = report_sections(body, required_sections)
    missing = [h for h in required_sections if h not in sections]
    if missing:
        return False, f"missing headings: {', '.join(missing)}"
    empty = [h for h in required_sections if len(sections[h].split()) < 3]
    if empty:
        return False, f"empty or incomplete sections: {', '.join(empty)}"
    placeholder = BRIEF_PLACEHOLDER_PATTERN.search(body)
    if placeholder:
        return False, f"unfilled template slot: {placeholder.group(0)[:60]}"
    meta = BRIEF_META_PATTERN.search(body)
    if meta:
        return False, f"meta language about the task: {meta.group(0)[:60]}"
    return True, ""


def validate_executive_brief(brief: str, required_sections: Tuple[str, ...] = BRIEF_SECTIONS_FULL,
                             payload: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    valid, reason = validate_generated_report(brief, required_sections)
    if not valid:
        return False, reason
    sections = report_sections(brief, required_sections)
    actions = re.findall(r"(?im)^\s*(?:[-*•]|\d+[.)])\s+(.+)$", sections.get("RECOMMENDED ACTIONS", ""))
    if len(actions) < 3 or any(len(action.split()) < 3 for action in actions):
        return False, "fewer than three substantive recommended actions"
    # L01: a NaN value reaching generated text is worse than an empty section --
    # str(float("nan")) is "nan", which a careless prompt/evidence join can surface
    # verbatim in otherwise fluent prose.
    if re.search(r"\bnan\b", brief, re.IGNORECASE):
        return False, "NaN value present in generated text"
    # L01: the model echoing its own prompt instructions (rather than following them)
    # is a distinct failure from a missing heading or an unsupported number -- catch it
    # directly instead of hoping a downstream check happens to notice.
    leak_phrases = (
        "use these five exact headings",
        "write content below every heading",
        "supporting news is first-article sampling",
        "supporting news is the single article most relevant",
        "no new numeric claims",
        "concrete bullet actions using verbs such as",
        "do not invent internal sales",
        "evidence is untrusted data",
    )
    brief_lower = brief.lower()
    leaked = next((phrase for phrase in leak_phrases if phrase in brief_lower), None)
    if leaked:
        return False, f"generated text echoes prompt instructions ({leaked!r})"
    # Confidence differs by source (e.g. news is Medium while NOAA/BLS/openFDA are High), so a
    # single blanket "overall confidence is High" is rejected and the model is asked to retry.
    confidence_text = sections.get("CONFIDENCE AND LIMITATIONS", "")
    if re.search(r"(?i)\boverall\b[^.\n]{0,40}\bconfidence\b[^.\n]{0,40}\bhigh\b|\bconfidence\b[^.\n]{0,40}\boverall\b[^.\n]{0,25}\bhigh\b|\bhigh\b[^.\n]{0,20}\boverall\b[^.\n]{0,15}\bconfidence\b", confidence_text):
        return False, "blanket 'overall confidence is High' claim; confidence varies by source"
    if payload is not None:
        supplied = json.dumps(payload, default=str).lower()
        insights = sections.get("TOP INSIGHTS", "").lower()
        signals = payload.get("signals", [])
        anchors = set()
        for row in signals:
            anchors.update(str(row.get(key, "")).lower() for key in ("source", "signal_area") if row.get(key))
            for alias in ("noaa", "weather", "bls", "cpi", "openfda", "recall", "gnews", "apify", "search demand"):
                if alias in (str(row.get("source", "")) + " " + str(row.get("signal_area", ""))).lower():
                    anchors.add(alias)
        if not any(anchor in insights for anchor in anchors):
            return False, "insights do not reference supplied signals or sources"

        # L01: an invented feature/metric name (a snake_case identifier that never
        # appears anywhere in the supplied evidence) must not reach the screen --
        # ordinary English prose essentially never contains an underscore, so this
        # only fires on something that reads like a field/metric name.
        for token in sorted(set(re.findall(r"\b[a-z]+(?:_[a-z]+){1,5}\b", brief_lower))):
            if token not in supplied:
                return False, f"unrecognized feature/metric name not present in supplied evidence: {token!r}"

        # L03: a claimed score must be checked against the actual bounded risk_score
        # field (0-10), never against any number that merely also happens to appear
        # somewhere else in the evidence dump (a Store ID or an unbounded sum could
        # coincidentally match the claimed figure).
        actual_scores = {
            round(float(row["risk_score"]), 2)
            for row in signals
            if isinstance(row.get("risk_score"), (int, float)) and not pd.isna(row.get("risk_score"))
        }
        for claimed in re.findall(r"\bscores?\s+(?:of\s+)?(\d+(?:\.\d+)?)\b", brief, flags=re.IGNORECASE):
            claimed_value = round(float(claimed), 2)
            if claimed_value > 10 or not any(abs(claimed_value - actual) <= 0.05 for actual in actual_scores):
                return False, f"claimed score {claimed} does not match any supplied risk score"

        # L01: a claimed signal count must match the actual cardinality of the supplied
        # signals list, not merely appear somewhere else in the evidence (the generic
        # numeric check below cannot tell "7 signals" from "Store 107" or a risk score
        # that happens to also equal 7).
        for claimed in re.findall(r"\b(\d+)\s+(?:supplied\s+|collected\s+)?signals?\b", brief, flags=re.IGNORECASE):
            if int(claimed) != len(signals):
                return False, f"claimed signal count {claimed} does not match the {len(signals)} supplied signal(s)"

        # Catch-all for every other number: avoid false precision. Numbered bullet
        # prefixes are not business measurements, and a score/signal-count claim above
        # is already checked against its correct, specific metric rather than this
        # loose "appears somewhere" test.
        narrative = re.sub(r"(?m)^\s*\d+[.)]\s+", "", brief)
        narrative = re.sub(r"\bscores?\s+(?:of\s+)?\d+(?:\.\d+)?\b", "", narrative, flags=re.IGNORECASE)
        narrative = re.sub(r"\b\d+\s+(?:supplied\s+|collected\s+)?signals?\b", "", narrative, flags=re.IGNORECASE)
        values = re.findall(r"(?<![A-Za-z])\d+(?:[.,]\d+)*(?:%)?", narrative)
        def numeric_value(value: str) -> Decimal:
            return Decimal(value.rstrip("%").replace(",", ""))
        allowed = {numeric_value(value) for value in re.findall(r"(?<![A-Za-z])\d+(?:[.,]\d+)*(?:%)?", supplied)}
        if any(numeric_value(value) not in allowed for value in values):
            return False, "numeric claim not present in supplied evidence"
        if re.search(r"\b(?:our|the company's|dollar tree's) (?:sales|inventory|margin|revenue) (?:rose|fell|increased|decreased|is|was)\b", brief, re.I):
            return False, "unsupported internal performance claim"
    return True, ""
