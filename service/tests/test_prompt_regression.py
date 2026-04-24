"""Sprint 1 / A7 — Prompt regression scaffold (opt-in).

Hits a real LLM and compares the response against expectations defined in
fixtures/prompt_regression.yaml. Only runs when RUN_LLM_REGRESSION=1 to
avoid spending budget on every CI run. Pinned to gpt-4o-mini at temp 0
to maximize reproducibility.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

_FIXTURE = Path(__file__).parent / "fixtures" / "prompt_regression.yaml"

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LLM_REGRESSION") != "1" or not _FIXTURE.exists(),
    reason="set RUN_LLM_REGRESSION=1 and provide fixtures/prompt_regression.yaml",
)


@pytest.mark.asyncio
async def test_prompt_regression_smoke() -> None:
    import yaml
    from services.llm import get_llm_client

    rows = yaml.safe_load(_FIXTURE.read_text()) or []
    assert rows, "prompt_regression.yaml is empty"

    llm = get_llm_client()
    # Cost guard: ~$0.15 per 1M input tokens for gpt-4o-mini.
    # Bail out before the run if the fixture would obviously bust the cap.
    max_total_input_tokens = 6_000_000
    est = sum(len(r.get("system", "")) + len(r["user"]) for r in rows) // 4
    assert est < max_total_input_tokens, (
        f"Estimated {est} input tokens exceeds prompt-regression cap"
    )

    failures: list[str] = []
    for row in rows:
        resp = await llm.complete(row.get("system", ""), row["user"])
        text = resp.content.lower()
        for needle in row.get("must_contain", []):
            if needle.lower() not in text:
                failures.append(f"{row['id']}: missing {needle!r}")
        for needle in row.get("must_not_contain", []):
            if needle.lower() in text:
                failures.append(f"{row['id']}: forbidden {needle!r} present")
    assert not failures, "Prompt regressions:\n  " + "\n  ".join(failures)
