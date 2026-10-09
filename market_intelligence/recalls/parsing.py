from typing import Any
from typing import List
from typing import Tuple
import re


def classify_recall(reason: str) -> Tuple[str, float]:
    text = (reason or "").lower()
    if any(word in text for word in ["lead", "heavy metal", "salmonella", "listeria", "e. coli", "contamination"]):
        return "high_safety_risk", 8.0
    if any(word in text for word in ["undeclared", "allergen", "milk", "peanut", "soy", "tree nut"]):
        return "allergen_risk", 6.5
    if any(word in text for word in ["mislabel", "label"]):
        return "labeling_risk", 4.5
    return "general_recall_risk", 5.0


_UPC_EXCLUSION_CONTEXT = re.compile(
    r"\b(lot|batch|exp|expir|best\s*by|use\s*by|sell\s*by|pack(?:ed)?\s*on|code\s*date)\b",
    flags=re.IGNORECASE,
)


_STANDARD_BARCODE_DIGIT_LENGTHS = {8, 12, 13, 14}  # UPC-E, UPC-A, EAN-13, GTIN-14


def extract_upcs(text: str) -> List[str]:
    """R03: openFDA's Food Enforcement dataset has no dedicated UPC field, so this parses
    free text (product description + code_info, the more structured of the two available
    fields -- see collect_fda_recalls). Precedence, most to least confident:

      1. A digit run explicitly labeled "UPC"/"UPC Code" in the text.
      2. (fallback, only if nothing was explicitly labeled) A bare digit run, but only at
         a standard barcode length (8/12/13/14 digits) and only when it is not sitting
         next to date/lot/batch context -- an arbitrary date, lot, or batch ID must never
         be interpreted as a UPC.
    """
    text = text or ""
    cleaned: List[str] = []

    labeled_candidates = re.findall(
        r"UPC(?:\s*Code)?[:\s#]*(\d(?:[\s-]?\d){7,13})", text, flags=re.IGNORECASE
    )
    for candidate in labeled_candidates:
        digits = re.sub(r"\D", "", candidate)
        if 8 <= len(digits) <= 14 and digits not in cleaned:
            cleaned.append(digits)
    if cleaned:
        return cleaned

    for match in re.finditer(r"\d(?:[\s-]?\d){7,13}", text):
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) not in _STANDARD_BARCODE_DIGIT_LENGTHS:
            continue
        context = text[max(0, match.start() - 20) : min(len(text), match.end() + 20)]
        if _UPC_EXCLUSION_CONTEXT.search(context):
            continue
        if digits not in cleaned:
            cleaned.append(digits)
    return cleaned


def extract_lots(text: str) -> List[str]:
    """Parse individual lot/batch codes from FDA recall text (kept as strings to preserve formatting)."""
    matches = re.findall(r"(?:lot|batch)(?:\s*(?:code|number|no\.?|#))?s?[:\s]+([A-Za-z0-9,;/\-\s]{2,80}?)(?:\.|;|$)", text or "", flags=re.IGNORECASE)
    lots: List[str] = []
    for match in matches:
        for token in re.split(r"[,;/]| and ", match):
            candidate = token.strip().strip(".")
            if candidate and 2 <= len(candidate) <= 20 and candidate not in lots:
                lots.append(candidate)
    return lots


def adjust_recall_score(base_score: float, classification: str, status: str) -> float:
    score = base_score
    class_text = normalize_recall_classification(classification)
    status_text = (status or "").lower()
    if class_text == "Class I":
        score += 1.5
    elif class_text == "Class II":
        score += 0.8
    if "ongoing" in status_text:
        score += 1.0
    elif "terminated" in status_text:
        score -= 1.0
    return round(min(10.0, max(1.0, score)), 2)


def normalize_recall_classification(value: Any) -> str:
    """Normalize FDA classification without treating Class II as Class I."""
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    if re.search(r"\bclass\s+iii\b", text) or re.fullmatch(r"iii", text):
        return "Class III"
    if re.search(r"\bclass\s+ii\b", text) or re.fullmatch(r"ii", text):
        return "Class II"
    if re.search(r"\bclass\s+i\b", text) or re.fullmatch(r"i", text):
        return "Class I"
    return "Not Classified"
