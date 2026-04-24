"""Sprint 4 / B8 — end-to-end RCA chain integration test.

Drives `services.rca.handle_rca` with a fake `LLMClient` that returns
canned step-1 and step-2 responses, and a stubbed `services.tools.call_tool`
so deterministic evidence gathering works without a DB. Confirms:

  * Step-1 hypotheses are parsed and forwarded to evidence gathering.
  * Tool calls are bounded by the per-hypothesis + total caps.
  * Step-2 final response_text and confidence are surfaced.
  * Cited IDs from step-2 prose appear in `used_sources`.
  * The full `RcaTrace` is populated (step1, step2, evidence_gathered).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from models.schemas import SourceCitation
from services import rca
from services.context_assembler import AssembledPrompt
from services.llm import ToolCallTrace, ToolEnabledResponse


# -----------------------------------------------------------------------------
# Fakes
# -----------------------------------------------------------------------------

class FakeLLM:
    """Returns canned ToolEnabledResponse objects in order, no network."""
    def __init__(self, *responses: ToolEnabledResponse) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.model_name = "fake:test"

    async def complete_with_tools(
        self, system_prompt: str, user_prompt: str, tools: list[dict[str, Any]],
        max_iters: int = 3, temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ToolEnabledResponse:
        self.calls.append({
            "system_prompt": system_prompt, "user_prompt": user_prompt,
            "tools": tools, "max_iters": max_iters,
        })
        return self._responses.pop(0)

    async def complete(self, *a, **kw):
        raise AssertionError("RCA chain should not call .complete() directly")


def _resp(content: str) -> ToolEnabledResponse:
    return ToolEnabledResponse(
        content=content,
        model="fake:test",
        prompt_tokens=10, completion_tokens=20, total_tokens=30,
        iterations=1,
        tool_calls=[],
        citations_collected=[],
    )


def _anchor():
    return SimpleNamespace(
        anchor_type="past_event",
        anchor_event_id="evt-1",
        anchor_run_id=None,
        anchor_time=datetime.now(timezone.utc),
        failure_mode_scope="sag",
        style_scope="A1",
        equipment_scope=[],
    )


def _assembled() -> AssembledPrompt:
    return AssembledPrompt(
        user_block="some assembled context with [3] mentioned",
        citations=[SourceCitation(id="3", type="DOCUMENT",
                                  title="WO 4521", excerpt="prior teardown")],
        summary={"documents": 1},
        excluded_buckets=[],
    )


# -----------------------------------------------------------------------------
# End-to-end test
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rca_chain_happy_path(monkeypatch):
    rca._clear_cache()

    # Stub the deterministic tool layer (B0) so evidence gathering returns
    # a citation we can later assert appears in the RCA trace.
    fake_evidence_citation = SourceCitation(
        id="t_pct_1", type="DISTRIBUTION",
        title="percentile result", excerpt="p95=200",
    )

    async def fake_call_tool(name: str, args: dict[str, Any]):
        from services.tools import ToolResult
        return ToolResult(
            ok=True,
            data={"name": name, "args": args, "p95": 200},
            citation=fake_evidence_citation,
        )

    monkeypatch.setattr("services.rca.call_tool", fake_call_tool)

    # Step 1 returns one hypothesis with one piece of evidence to gather.
    step1_json = (
        '{"hypotheses": [{'
        '  "cause_label": "polymer drift",'
        '  "rationale_short": "viscosity above baseline",'
        '  "evidence_already_seen": ["3"],'
        '  "evidence_to_gather": ['
        '    {"kind": "percentile",'
        '     "arguments": {"tag_name": "viscosity"},'
        '     "rationale": "check tail"}'
        '  ],'
        '  "prior_probability": 0.6'
        '}]}'
    )
    # Step 2 produces a final report citing both the original [3] and
    # the evidence-gathered [t_pct_1].
    step2_text = (
        "## Adjudication\n"
        "- HYPOTHESIS 1 (polymer drift): LIKELY. Supported by [3] and [t_pct_1].\n\n"
        "## Recommended verification steps\n"
        "- Inspect WO 4521 §Resolution.\n\n"
        "CONFIDENCE: LIKELY"
    )
    fake_llm = FakeLLM(_resp(step1_json), _resp(step2_text))

    outcome = await rca.handle_rca(
        llm=fake_llm,
        query="Why did the sag happen?",
        anchor=_anchor(),
        assembled=_assembled(),
    )

    # ------------ assertions ----------------------------------------------
    assert outcome.confidence == "likely"
    assert "CONFIDENCE: LIKELY" in outcome.response_text
    # Two LLM calls — step1 + step2 — each contributing tokens.
    assert outcome.total_tokens == 60
    assert outcome.prompt_version == "rca_step1_v1+rca_step2_v1"
    # Cited IDs from response_text picked up both buckets.
    cited_ids = {s.id for s in outcome.used_sources}
    assert "3" in cited_ids                # original assembled citation
    assert "t_pct_1" in cited_ids          # evidence gathered citation
    # New (RCA-only) citations include the evidence one.
    assert any(c.id == "t_pct_1" for c in outcome.new_citations)

    trace = outcome.rca_trace
    assert trace.cache_hit_step1 is False
    assert trace.total_tool_calls == 1     # one EvidenceRequest gathered
    assert len(trace.step1["hypotheses"]) == 1
    assert trace.step1["hypotheses"][0]["cause_label"] == "polymer drift"
    assert len(trace.evidence_gathered) == 1
    assert trace.evidence_gathered[0]["result_citation_id"] == "t_pct_1"
    assert trace.step2["raw_response"].startswith("## Adjudication")


@pytest.mark.asyncio
async def test_rca_chain_caches_step1_on_repeat(monkeypatch):
    """Second call against the same anchor must not re-invoke step 1 LLM."""
    rca._clear_cache()

    async def fake_call_tool(name: str, args: dict[str, Any]):
        from services.tools import ToolResult
        return ToolResult(ok=True, data={}, citation=None)

    monkeypatch.setattr("services.rca.call_tool", fake_call_tool)

    step1_json = (
        '{"hypotheses": [{'
        '  "cause_label": "x", "rationale_short": "y",'
        '  "evidence_to_gather": [], "prior_probability": 0.5'
        '}]}'
    )
    step2_text = "Final.\n\nCONFIDENCE: HYPOTHESIS"
    # First run: step1 + step2. Second run: ONLY step2 (cached step1).
    fake_llm = FakeLLM(
        _resp(step1_json), _resp(step2_text),
        _resp(step2_text),
    )

    anchor = _anchor()
    out1 = await rca.handle_rca(
        llm=fake_llm, query="Why?", anchor=anchor, assembled=_assembled(),
    )
    assert out1.rca_trace.cache_hit_step1 is False
    assert len(fake_llm.calls) == 2

    out2 = await rca.handle_rca(
        llm=fake_llm, query="Why?", anchor=anchor, assembled=_assembled(),
    )
    assert out2.rca_trace.cache_hit_step1 is True
    # Only step2 was invoked the second time.
    assert len(fake_llm.calls) == 3


@pytest.mark.asyncio
async def test_rca_chain_caps_evidence_per_hypothesis(monkeypatch):
    """Per-hypothesis evidence cap must clip oversized evidence_to_gather."""
    rca._clear_cache()
    from config.settings import get_settings
    monkeypatch.setattr(get_settings(), "rca_max_evidence_per_hypothesis", 2)
    monkeypatch.setattr(get_settings(), "rca_max_total_tool_calls", 100)

    calls: list[str] = []

    async def fake_call_tool(name: str, args: dict[str, Any]):
        calls.append(name)
        from services.tools import ToolResult
        return ToolResult(ok=True, data={}, citation=None)

    monkeypatch.setattr("services.rca.call_tool", fake_call_tool)

    # 5 evidence items requested — cap should clip to 2.
    items = [
        ('{"kind": "percentile", "arguments": {}, "rationale": ""}')
        for _ in range(5)
    ]
    step1_json = (
        '{"hypotheses": [{'
        '  "cause_label": "x", "rationale_short": "y",'
        '  "evidence_to_gather": [' + ",".join(items) + '],'
        '  "prior_probability": 0.5'
        '}]}'
    )
    fake_llm = FakeLLM(_resp(step1_json), _resp("done\n\nCONFIDENCE: HYPOTHESIS"))

    out = await rca.handle_rca(
        llm=fake_llm, query="Why?", anchor=_anchor(), assembled=_assembled(),
    )
    assert len(calls) == 2
    assert out.rca_trace.total_tool_calls == 2
