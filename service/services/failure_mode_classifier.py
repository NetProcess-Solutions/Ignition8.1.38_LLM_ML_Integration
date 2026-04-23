"""
Failure-mode classifier interface (design §4.3 taxonomy discipline note).

The existing multi-agent delam analysis system (DELAM_0003 etc.) already
classifies failure modes from free-text descriptions. We expose a narrow
interface here so historical defect re-tagging (Task 4) and live ingestion
can call it without coupling to the upstream system. A stub
implementation is provided for tests; production wiring is a single
adapter behind this protocol.

All assignments must pass engineer review before flipping the
defect_events.failure_mode column — this module returns *proposed*
classifications only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


@dataclass
class ProposedClassification:
    """A proposed failure_mode for a defect_events row."""
    defect_id: str
    proposed_failure_mode: str
    confidence: float          # 0.0 – 1.0
    rationale: str             # human-readable
    raw_score: dict | None = None


class FailureModeClassifier(Protocol):
    """The interface every classifier implementation must satisfy."""

    def classify(self, description: str, defect_type: str | None = None) -> ProposedClassification | None:
        ...


# ---------------------------------------------------------------------------
# Heuristic stub — a deterministic keyword classifier mirroring the seed
# enum. Used in tests and as a fallback if the upstream multi-agent system
# is unavailable. Its proposals always carry confidence ≤ 0.7 so engineer
# review is the gating step.
# ---------------------------------------------------------------------------

_HEURISTIC_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\bhot\s*pull|hotpull|hot-pull\b", re.I), "delam_hotpull", "matched 'hot pull'"),
    (re.compile(r"\bcold\s+delam|delam.*cold\b", re.I),    "delam_cold",     "matched 'cold delam'"),
    (re.compile(r"\bdelam(?:ination)?\b", re.I),           "delam_hotpull", "matched 'delam' (default to hotpull)"),
    (re.compile(r"\bedge\s*fold|off[- ]?tenter\b", re.I), "off_tenter_edge_fold", "matched 'edge fold/off-tenter'"),
    (re.compile(r"\bprecoat.*bubble|bubble.*precoat\b", re.I), "bubble_precoat", "matched 'precoat bubble'"),
    (re.compile(r"\bbubble|bubbling\b", re.I),             "bubble_adhesive", "matched 'bubble' (default adhesive)"),
    (re.compile(r"\bstreak.*(front|back)|front[- ]back\b", re.I),
        "streak_frontback", "matched 'front-back streak'"),
    (re.compile(r"\bcoating\s+weight|cw\s*out\s*of\s*spec|ozsy|oz/sy\b", re.I),
        "cw_out_of_spec", "matched 'coating weight / ozsy'"),
    (re.compile(r"\bcontamination\b", re.I),               "contamination_other", "matched 'contamination'"),
]


class HeuristicFailureModeClassifier:
    """Stub classifier; deterministic, used until the multi-agent system is wired."""

    def classify(
        self, description: str, defect_type: str | None = None
    ) -> ProposedClassification | None:
        if not description:
            return None
        for rx, code, why in _HEURISTIC_RULES:
            if rx.search(description):
                return ProposedClassification(
                    defect_id="",  # caller fills in
                    proposed_failure_mode=code,
                    confidence=0.7,
                    rationale=f"heuristic: {why}",
                )
        # Catch-all so re-tagging always proposes *something*; engineer
        # review will recategorize 'other' rows by hand.
        return ProposedClassification(
            defect_id="",
            proposed_failure_mode="other",
            confidence=0.2,
            rationale="no heuristic matched; defaulting to 'other' for review",
        )


def get_default_classifier() -> FailureModeClassifier:
    """Factory; future: read from settings to swap in the multi-agent adapter."""
    return HeuristicFailureModeClassifier()
