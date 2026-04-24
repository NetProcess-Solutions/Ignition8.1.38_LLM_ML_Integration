"""Sprint 1 / A7 — Retrieval golden-set scaffold.

Each fixture row pairs a natural-language query with one chunk_id that
MUST appear in the top-k retrieved set. Runs only when both:

    1. RUN_RETRIEVAL_GOLDEN=1 in the environment, AND
    2. fixtures/retrieval_golden.yaml exists with at least one entry.

The recall-at-k floor is loaded from
fixtures/retrieval_golden_baseline.json so we can detect regressions
even before the corpus is fully loaded.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

_FIXTURE = Path(__file__).parent / "fixtures" / "retrieval_golden.yaml"
_BASELINE = Path(__file__).parent / "fixtures" / "retrieval_golden_baseline.json"

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_RETRIEVAL_GOLDEN") != "1" or not _FIXTURE.exists(),
    reason="set RUN_RETRIEVAL_GOLDEN=1 and provide fixtures/retrieval_golden.yaml",
)


@pytest.mark.asyncio
async def test_retrieval_recall_at_10_meets_baseline() -> None:
    import yaml
    from db.connection import SessionFactory
    from services.retrieval import retrieve_chunks

    rows = yaml.safe_load(_FIXTURE.read_text()) or []
    assert rows, "retrieval_golden.yaml is empty"

    baseline = json.loads(_BASELINE.read_text()) if _BASELINE.exists() else {}
    floor = float(baseline.get("recall_at_10", 0.6))

    hits = 0
    async with SessionFactory() as session:
        for row in rows:
            top = await retrieve_chunks(session, row["query"], k=10)
            ids = {str(c.chunk_id) for c in top}
            if row["must_retrieve_chunk_id"] in ids:
                hits += 1
    recall = hits / len(rows)
    assert recall >= floor, (
        f"recall@10={recall:.2f} regressed below baseline {floor:.2f}"
    )
