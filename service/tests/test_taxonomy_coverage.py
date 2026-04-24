"""Sprint 1 / A7 — Failure-mode taxonomy coverage.

Asserts that every failure_mode code seeded in the database is reachable
from at least one of the keyword maps that drive RCA today:

  * `services.anchor.FAILURE_MODE_KEYWORDS`           (query-time anchoring)
  * `services.failure_mode_classifier._HEURISTIC_RULES` (defect classification)

Why this matters: silent gaps here mean a real defect type can never be
auto-anchored or auto-classified, producing perpetually empty RAG context
for that failure mode — the worst class of bug because it never errors.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.anchor import FAILURE_MODE_KEYWORDS
from services.failure_mode_classifier import _HEURISTIC_RULES


SEED_SQL = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "seed_reference_data.sql"
)

# Codes intentionally not reachable from heuristics:
#   'other'  — operator-assigned only (catch-all)
HEURISTIC_EXEMPT: set[str] = {"other"}

# Codes intentionally not reachable from anchor keywords:
#   'other'                — catch-all
#   'contamination_other'  — too generic to anchor a chat query on
ANCHOR_EXEMPT: set[str] = {"other", "contamination_other"}


def _seeded_codes() -> set[str]:
    sql = SEED_SQL.read_text()
    block = sql.split("INSERT INTO failure_modes", 1)[1]
    block = block.split("ON CONFLICT", 1)[0]
    return set(re.findall(r"\(\s*'([a-z0-9_]+)'\s*,", block))


SEEDED = _seeded_codes()


def test_seed_file_has_codes() -> None:
    assert SEEDED, f"No failure_mode codes parsed from {SEED_SQL}"


@pytest.mark.parametrize("code", sorted(SEEDED - ANCHOR_EXEMPT))
def test_anchor_keyword_covers_code(code: str) -> None:
    assert code in set(FAILURE_MODE_KEYWORDS.values()), (
        f"failure_mode '{code}' has no keyword in anchor.FAILURE_MODE_KEYWORDS; "
        "queries about it can never auto-anchor a failure mode."
    )


@pytest.mark.parametrize("code", sorted(SEEDED - HEURISTIC_EXEMPT))
def test_heuristic_classifier_covers_code(code: str) -> None:
    classified_codes = {entry[1] for entry in _HEURISTIC_RULES}
    assert code in classified_codes, (
        f"failure_mode '{code}' has no rule in failure_mode_classifier; "
        "defect events of this type will never be auto-classified."
    )
