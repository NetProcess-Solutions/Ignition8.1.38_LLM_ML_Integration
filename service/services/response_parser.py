"""Confidence parsing and citation enforcement on LLM responses."""
from __future__ import annotations

import re
from typing import Literal

ConfidenceLabel = Literal["confirmed", "likely", "hypothesis", "insufficient_evidence"]

_CONFIDENCE_RE = re.compile(
    r"\bCONFIDENCE\s*:\s*(CONFIRMED|LIKELY|HYPOTHESIS|INSUFFICIENT[_ ]EVIDENCE)\b",
    re.IGNORECASE,
)
_CITATION_RE = re.compile(r"\[(\d+)\]")


def parse_confidence(text: str) -> ConfidenceLabel:
    m = _CONFIDENCE_RE.search(text)
    if not m:
        return "hypothesis"
    raw = m.group(1).upper().replace(" ", "_")
    if raw == "CONFIRMED":
        return "confirmed"
    if raw == "LIKELY":
        return "likely"
    if raw == "HYPOTHESIS":
        return "hypothesis"
    return "insufficient_evidence"


def extract_cited_ids(text: str) -> set[str]:
    return set(_CITATION_RE.findall(text))


def has_any_citations(text: str) -> bool:
    return bool(_CITATION_RE.search(text))
