"""
Sprint 6 / B4 — Offline evaluation harness (PLACEHOLDER).

Replays a YAML dataset of (query, expected_citation_ids,
expected_failure_mode, expected_confidence_floor) cases through the
running service and computes:

  * Citation precision / recall vs. expected
  * Failure-mode classification accuracy (where expected_failure_mode
    is set)
  * Confidence-floor honored (% of cases where the assistant said at
    least the floor; a "confirmed" answer when only "hypothesis" was
    expected is a downgrade-tracking signal)
  * RCA precision (when expected_root_cause is set in the case file)

Why this is a stub:
  Needs a curated label set (~200 cases minimum) drawn from real
  historical events. The schema below is the contract the labeller
  tool should produce.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# TODO(B4): drop a real dataset under fixtures/eval/ and point this at it.
DEFAULT_DATASET_PATH = Path(__file__).parent / "fixtures" / "eval" / "v1.yaml"


@dataclass
class EvalCase:
    """One labeled query + expected behavior."""
    case_id: str
    query: str
    line_id: str
    # Expected: citations contain at least one of these document/source ids
    expected_citation_ids: list[str] = field(default_factory=list)
    expected_failure_mode: str | None = None
    # "confirmed" / "likely" / "hypothesis" / "insufficient_evidence"
    expected_confidence_floor: str | None = None
    # Free-text reference RCA explanation, used only for human review
    reference_root_cause: str | None = None
    # If set, the eval will replay against this seed snapshot id (a row
    # in feature_snapshots that captures the historian state at the
    # event time) instead of "now".
    snapshot_id: str | None = None


@dataclass
class EvalResult:
    case_id: str
    passed: bool
    citation_precision: float
    citation_recall: float
    confidence_actual: str
    confidence_passed: bool
    failure_mode_passed: bool | None
    notes: str = ""


def load_cases(path: Path | None = None) -> list[EvalCase]:
    """Load eval cases from YAML. Stub raises with instructions."""
    p = path or DEFAULT_DATASET_PATH
    if not p.exists():
        raise FileNotFoundError(
            f"No eval dataset at {p}. Create one with the schema in EvalCase. "
            "Recommended: 200+ cases drawn from the last 6 months of real "
            "downtime/defect events, labelled by an operator + engineer pair."
        )
    # TODO(B4): implement YAML load + Pydantic validation.
    raise NotImplementedError("B4 eval harness not implemented")


async def run_eval(
    cases: list[EvalCase],
    *,
    base_url: str = "http://localhost:8080",
    api_key: str = "",
) -> list[EvalResult]:
    """Replay each case against /api/chat and score the responses."""
    # TODO(B4): implement.
    #   for c in cases:
    #       resp = await httpx_post(f"{base_url}/api/chat", json={...})
    #       result = score(c, resp)
    #       results.append(result)
    raise NotImplementedError("B4 eval harness not implemented")


def summarize(results: list[EvalResult]) -> dict[str, Any]:
    """Aggregate citation P/R, confidence honored, FM accuracy."""
    # TODO(B4): implement summary stats.
    raise NotImplementedError("B4 eval harness not implemented")


# Example dataset shape (drop into fixtures/eval/v1.yaml):
EXAMPLE_DATASET_YAML = """\
# Eval cases for B4 harness. Each entry is one EvalCase.
- case_id: "edge_sag_2025-12-04"
  query: "Why did the right edge sag at 14:32 yesterday on coater 1?"
  line_id: "coater1"
  expected_citation_ids: ["doc:wo-4521", "event:defect-9912"]
  expected_failure_mode: "edge_sag"
  expected_confidence_floor: "likely"
  reference_root_cause: "Tenter chain tension was 15% below recipe target."
  snapshot_id: "fs-7c4a..."

- case_id: "blister_after_oven_step5"
  query: "What's behind the blistering on style A1 today?"
  line_id: "coater1"
  expected_citation_ids: ["doc:sop-coating-007"]
  expected_failure_mode: "blister"
  expected_confidence_floor: "hypothesis"
"""
