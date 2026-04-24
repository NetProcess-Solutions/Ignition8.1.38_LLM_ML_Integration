"""
Sprint 4 / B8 — RCA reasoning chain.

A two-step LLM workflow that replaces one-shot RAG when the parsed
anchor indicates a defect/downtime root-cause investigation.

Public entry: `handle_rca(req, anchor, phase1)` returns an `RcaOutcome`
shaped like the rest of `rag.handle_chat`'s post-LLM phase 2 output —
so `rag.handle_chat` can switch on it and persist with the same code.

Triggering is decided in `should_use_rca_chain()` and called from
`rag.handle_chat`. If RCA is not triggered the one-shot path runs
unchanged.

Design (matches plan.md B8):
  Step 1 — Hypothesis generation (LLM call #1, may use tools).
  Step 2 — Adjudication (LLM call #2, may use tools).
  In between: deterministic evidence gathering (no LLM, just tools).
  All under a hard tool-call budget; both LLM calls' traces are
  written to messages.context_snapshot.rca_trace.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from config.settings import get_settings
from models.schemas import (
    EvidenceRequest,
    RcaHypothesis,
    RcaTrace,
    SourceCitation,
)
from services.context_assembler import AssembledPrompt
from services.llm import LLMClient, ToolCallTrace, ToolEnabledResponse
from services.tools import call_tool, openai_tool_specs

_log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Triggering
# ---------------------------------------------------------------------------

# Causal-intent markers; deliberately conservative so day-to-day "what's
# happening" / "is X normal" queries don't flip into the heavier chain.
_RCA_QUERY_RE = re.compile(
    r"\b(why|what\s+caused|root\s+cause|reason|how\s+did|what\s+made|"
    r"what's\s+behind|account\s+for|explain)\b",
    re.IGNORECASE,
)


def should_use_rca_chain(query: str, anchor: Any) -> bool:
    s = get_settings()
    if not s.rca_chain_enabled:
        return False
    if getattr(anchor, "anchor_type", "") != "past_event":
        return False
    has_event = bool(getattr(anchor, "anchor_event_id", None))
    has_fm = bool(getattr(anchor, "failure_mode_scope", None))
    if not (has_event or has_fm):
        return False
    return bool(_RCA_QUERY_RE.search(query or ""))


# ---------------------------------------------------------------------------
# Prompts (kept inline; pinned via prompt_version embedded in the trace)
# ---------------------------------------------------------------------------

PROMPT_VERSION_STEP1 = "rca_step1_v1"
PROMPT_VERSION_STEP2 = "rca_step2_v1"

_RCA_TOOL_ALLOWLIST = {
    "percentile_of",
    "compare_to_distribution",
    "nearest_historical_runs",
    "detect_drift",
    "defect_events_in_window",
}

SYSTEM_PROMPT_STEP1 = """\
You are a senior process engineer doing a root-cause investigation.

You have just been given the curated context for a past production
event (a defect or downtime). Your job in THIS step is ONLY to:

  1. Enumerate up to N candidate root causes (hypotheses), each with a
     short rationale grounded in the evidence already shown.
  2. For each hypothesis, list the additional evidence you would gather
     to confirm or reject it. Each evidence item MUST use the closed
     `EvidenceRequest` schema below — never invent tool names.

Do NOT produce a final answer in this step. You are NOT writing the
report; you are scoping the investigation.

You MAY call tools to ground your hypotheses in distributional facts.
Tool calls are bounded; prefer hypotheses you can evidence cheaply.

Reply with ONLY a JSON object matching this schema:

{
  "hypotheses": [
    {
      "cause_label": "<short cause name, ≤ 10 words>",
      "rationale_short": "<≤ 2 sentences>",
      "evidence_already_seen": ["<citation_id>", ...],
      "evidence_to_gather": [
        {"kind": "percentile|compare_to_distribution|nearest_historical_runs|detect_drift|defect_events_in_window|chunk_search",
         "arguments": {...},
         "rationale": "<why this evidence helps>"}
      ],
      "prior_probability": <0..1>
    }
  ]
}

If the curated context is insufficient to even propose hypotheses,
return {"hypotheses": []}.
"""

SYSTEM_PROMPT_STEP2 = """\
You are a senior process engineer producing a root-cause-analysis
report. You have:

  * The original user query
  * The originally curated context (the RAG bundle)
  * Per-hypothesis evidence sections from a deterministic gathering
    phase
  * Optional ad-hoc tool-call results (you may make up to a handful
    more)

Adjudicate the hypotheses. Your final answer MUST:

  * Cite each claim using the `[id]` citation tags from the assembled
    context AND any new tool-call citation IDs.
  * Label every hypothesis CONFIRMED / LIKELY / HYPOTHESIS / REJECTED
    with one-line justification.
  * Conclude with a "Recommended verification steps" section. These
    are READ-ONLY actions ("inspect WO 4521 §Resolution", "pull last
    24 h of historian for tag X"). NEVER recommend a control action
    (do not say "set", "change", "raise/lower", "stop", "start").
  * End with a single line: `CONFIDENCE: <CONFIRMED|LIKELY|HYPOTHESIS|INSUFFICIENT_EVIDENCE>`.

If the gathered evidence does not support any hypothesis, label all
HYPOTHESIS or REJECTED and emit `CONFIDENCE: INSUFFICIENT_EVIDENCE`.
"""


# ---------------------------------------------------------------------------
# In-process cache (TTL) for repeat queries about the same anchor
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    hypotheses: list[RcaHypothesis]
    fetched_at: float = field(default_factory=time.time)


_STEP1_CACHE: dict[str, _CacheEntry] = {}


def _cache_key(anchor: Any) -> str:
    raw = "|".join(
        (
            str(getattr(anchor, "anchor_event_id", "") or ""),
            str(getattr(anchor, "anchor_run_id", "") or ""),
            (
                getattr(anchor, "anchor_time", None).isoformat()
                if getattr(anchor, "anchor_time", None)
                else ""
            ),
            str(getattr(anchor, "failure_mode_scope", "") or ""),
            PROMPT_VERSION_STEP1,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(anchor: Any) -> list[RcaHypothesis] | None:
    s = get_settings()
    key = _cache_key(anchor)
    entry = _STEP1_CACHE.get(key)
    if entry is None:
        return None
    if time.time() - entry.fetched_at > s.rca_cache_ttl_seconds:
        _STEP1_CACHE.pop(key, None)
        return None
    return entry.hypotheses


def _cache_put(anchor: Any, hyps: list[RcaHypothesis]) -> None:
    _STEP1_CACHE[_cache_key(anchor)] = _CacheEntry(hypotheses=hyps)


# ---------------------------------------------------------------------------
# Outcome shape — mirrors what rag.handle_chat needs after the LLM call
# ---------------------------------------------------------------------------

@dataclass
class RcaOutcome:
    response_text: str
    confidence: str
    used_sources: list[SourceCitation]
    new_citations: list[SourceCitation]
    prompt_version: str
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    llm_ms: int
    rca_trace: RcaTrace


# ---------------------------------------------------------------------------
# Step 1 — hypothesis generation
# ---------------------------------------------------------------------------

async def _step1_hypotheses(
    *, llm: LLMClient, assembled: AssembledPrompt, query: str,
) -> tuple[list[RcaHypothesis], ToolEnabledResponse]:
    s = get_settings()
    user_block = (
        f"USER QUERY:\n{query}\n\n"
        f"=== ASSEMBLED CONTEXT (use [id] tags as evidence_already_seen) ===\n"
        f"{assembled.user_block}\n\n"
        f"Return JSON only. Up to {s.rca_max_hypotheses} hypotheses."
    )
    resp = await llm.complete_with_tools(
        system_prompt=SYSTEM_PROMPT_STEP1,
        user_prompt=user_block,
        tools=openai_tool_specs(allowlist=_RCA_TOOL_ALLOWLIST),
        max_iters=s.rca_step1_max_iters,
        temperature=0.1,
    )
    hyps = _parse_hypotheses(resp.content, s.rca_max_hypotheses)
    return hyps, resp


def _parse_hypotheses(text_blob: str, cap: int) -> list[RcaHypothesis]:
    """Extract a JSON object from the LLM output. Tolerates code fences."""
    if not text_blob:
        return []
    candidate = text_blob.strip()
    # Strip ```json fences if present.
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\n?", "", candidate)
        candidate = re.sub(r"\n?```\s*$", "", candidate)
    # Otherwise grab the first {...} block.
    if not candidate.startswith("{"):
        m = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not m:
            return []
        candidate = m.group(0)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as e:
        _log.warn("rca_step1_bad_json", err=str(e))
        return []
    raw_list = parsed.get("hypotheses") if isinstance(parsed, dict) else None
    if not isinstance(raw_list, list):
        return []
    out: list[RcaHypothesis] = []
    for item in raw_list[:cap]:
        try:
            out.append(RcaHypothesis.model_validate(item))
        except Exception as e:
            _log.warn("rca_step1_skip_hypothesis", err=str(e), item=item)
    return out


# ---------------------------------------------------------------------------
# Evidence gathering — deterministic, bounded
# ---------------------------------------------------------------------------

# Map LLM-side EvidenceRequestKind → tool name in the registry.
_KIND_TO_TOOL: dict[str, str] = {
    "percentile":               "percentile_of",
    "compare_to_distribution":  "compare_to_distribution",
    "nearest_historical_runs":  "nearest_historical_runs",
    "detect_drift":             "detect_drift",
    "defect_events_in_window":  "defect_events_in_window",
    # chunk_search is intentionally unsupported here; the retrieval layer
    # already serves it via the assembled context. Surface as a no-op.
}


@dataclass
class _GatheredEvidenceItem:
    hypothesis_idx: int
    request: EvidenceRequest
    citation: SourceCitation | None
    result_data: dict[str, Any]
    error: str | None


async def _gather_evidence(
    hypotheses: list[RcaHypothesis],
) -> list[_GatheredEvidenceItem]:
    s = get_settings()
    out: list[_GatheredEvidenceItem] = []
    total = 0
    for hi, h in enumerate(hypotheses):
        per_h_left = s.rca_max_evidence_per_hypothesis
        for ev in h.evidence_to_gather:
            if per_h_left <= 0:
                break
            if total >= s.rca_max_total_tool_calls:
                break
            tool_name = _KIND_TO_TOOL.get(ev.kind)
            if tool_name is None:
                out.append(_GatheredEvidenceItem(
                    hypothesis_idx=hi, request=ev,
                    citation=None, result_data={},
                    error=f"unsupported evidence kind: {ev.kind}",
                ))
                continue
            result = await call_tool(tool_name, ev.arguments or {})
            out.append(_GatheredEvidenceItem(
                hypothesis_idx=hi,
                request=ev,
                citation=result.citation,
                result_data=result.data,
                error=result.error,
            ))
            per_h_left -= 1
            total += 1
        if total >= s.rca_max_total_tool_calls:
            break
    return out


def _render_evidence_section(
    hypotheses: list[RcaHypothesis],
    gathered: list[_GatheredEvidenceItem],
) -> tuple[str, list[SourceCitation]]:
    if not hypotheses:
        return "", []
    lines: list[str] = []
    new_cits: list[SourceCitation] = []
    for hi, h in enumerate(hypotheses, start=1):
        lines.append(f"=== EVIDENCE FOR HYPOTHESIS {hi}: {h.cause_label} ===")
        items = [g for g in gathered if g.hypothesis_idx == hi - 1]
        if not items:
            lines.append("(no additional evidence gathered)")
            lines.append("")
            continue
        for g in items:
            if g.error:
                lines.append(f"- [{g.request.kind}] ERROR: {g.error}")
                continue
            cid = g.citation.id if g.citation else "?"
            excerpt = g.citation.excerpt if g.citation else json.dumps(g.result_data)[:200]
            lines.append(f"- [{cid}] {g.request.kind}: {excerpt}")
            if g.citation is not None:
                new_cits.append(g.citation)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n", new_cits


# ---------------------------------------------------------------------------
# Step 2 — adjudication
# ---------------------------------------------------------------------------

_CONFIDENCE_RE = re.compile(
    r"CONFIDENCE:\s*(CONFIRMED|LIKELY|HYPOTHESIS|INSUFFICIENT_EVIDENCE)",
    re.IGNORECASE,
)


def _parse_step2_confidence(text_blob: str) -> str:
    m = _CONFIDENCE_RE.search(text_blob or "")
    if not m:
        return "hypothesis"
    raw = m.group(1).upper()
    return {
        "CONFIRMED": "confirmed",
        "LIKELY": "likely",
        "HYPOTHESIS": "hypothesis",
        "INSUFFICIENT_EVIDENCE": "insufficient_evidence",
    }[raw]


async def _step2_adjudication(
    *,
    llm: LLMClient,
    query: str,
    assembled: AssembledPrompt,
    hypotheses: list[RcaHypothesis],
    evidence_section: str,
) -> ToolEnabledResponse:
    s = get_settings()
    hyps_block = json.dumps(
        [h.model_dump() for h in hypotheses], default=str, indent=2,
    )
    user_block = (
        f"USER QUERY:\n{query}\n\n"
        f"=== ORIGINAL CURATED CONTEXT ===\n{assembled.user_block}\n\n"
        f"=== STEP 1 HYPOTHESES (from earlier LLM call) ===\n{hyps_block}\n\n"
        f"{evidence_section}\n"
        "Now adjudicate per the system instructions and produce the final report."
    )
    return await llm.complete_with_tools(
        system_prompt=SYSTEM_PROMPT_STEP2,
        user_prompt=user_block,
        tools=openai_tool_specs(allowlist=_RCA_TOOL_ALLOWLIST),
        max_iters=s.rca_step2_max_iters,
        temperature=0.1,
    )


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------

async def handle_rca(
    *,
    llm: LLMClient,
    query: str,
    anchor: Any,
    assembled: AssembledPrompt,
) -> RcaOutcome:
    """Run the two-step RCA chain. Returns an `RcaOutcome` to be persisted
    by the caller using the same path as the one-shot LLM response."""
    t0 = time.perf_counter()

    # Step 1 — try cache first (skip the LLM call when fresh).
    cached = _cache_get(anchor)
    if cached is not None:
        hypotheses = cached
        step1_resp: ToolEnabledResponse | None = None
        cache_hit = True
    else:
        hypotheses, step1_resp = await _step1_hypotheses(
            llm=llm, assembled=assembled, query=query,
        )
        if hypotheses:
            _cache_put(anchor, hypotheses)
        cache_hit = False

    # Evidence — deterministic.
    gathered = await _gather_evidence(hypotheses) if hypotheses else []
    evidence_section, evidence_citations = _render_evidence_section(hypotheses, gathered)

    # Step 2 — adjudication LLM call (always runs; even no-hypothesis case
    # produces a graceful "insufficient evidence" answer).
    step2 = await _step2_adjudication(
        llm=llm,
        query=query,
        assembled=assembled,
        hypotheses=hypotheses,
        evidence_section=evidence_section,
    )

    response_text = step2.content.strip() or (
        "INSUFFICIENT_EVIDENCE: the gathered evidence does not support a "
        "confident root cause.\n\nCONFIDENCE: INSUFFICIENT_EVIDENCE"
    )
    confidence = _parse_step2_confidence(response_text)

    # Sources actually used = original assembled cites by id-mention +
    # any new tool-call citations from step 1 (if it ran), step 2, and
    # the deterministic evidence-gather phase.
    cited_ids = set(re.findall(r"\[([A-Za-z0-9._:-]+)\]", response_text))
    new_cits = list(evidence_citations) + list(step2.citations_collected)
    if step1_resp is not None:
        new_cits += list(step1_resp.citations_collected)

    # De-dup new_cits by id so the same tool result doesn't double-cite.
    seen: set[str] = set()
    dedup_new: list[SourceCitation] = []
    for c in new_cits:
        if c.id in seen:
            continue
        seen.add(c.id)
        dedup_new.append(c)

    used_sources: list[SourceCitation] = [
        c for c in (assembled.citations + dedup_new) if c.id in cited_ids
    ]

    total_tokens = step2.total_tokens + (step1_resp.total_tokens if step1_resp else 0)
    prompt_tokens = step2.prompt_tokens + (step1_resp.prompt_tokens if step1_resp else 0)
    completion_tokens = step2.completion_tokens + (
        step1_resp.completion_tokens if step1_resp else 0
    )
    total_tool_calls = (
        len(gathered)
        + len(step2.tool_calls)
        + (len(step1_resp.tool_calls) if step1_resp else 0)
    )
    llm_ms = int((time.perf_counter() - t0) * 1000)

    trace = RcaTrace(
        step1={
            "prompt_version": PROMPT_VERSION_STEP1,
            "model": llm.model_name,
            "cache_hit": cache_hit,
            "hypotheses": [h.model_dump() for h in hypotheses],
            "tool_calls": (
                [_trace_call(t) for t in step1_resp.tool_calls]
                if step1_resp else []
            ),
            "raw_response": (step1_resp.content if step1_resp else ""),
        },
        evidence_gathered=[
            {
                "hypothesis_idx": g.hypothesis_idx,
                "request": g.request.model_dump(),
                "result_citation_id": g.citation.id if g.citation else None,
                "error": g.error,
            }
            for g in gathered
        ],
        step2={
            "prompt_version": PROMPT_VERSION_STEP2,
            "model": llm.model_name,
            "raw_response": step2.content,
            "tool_calls": [_trace_call(t) for t in step2.tool_calls],
        },
        cache_hit_step1=cache_hit,
        total_llm_tokens=total_tokens,
        total_tool_calls=total_tool_calls,
    )

    return RcaOutcome(
        response_text=response_text,
        confidence=confidence,
        used_sources=used_sources,
        new_citations=dedup_new,
        prompt_version=f"{PROMPT_VERSION_STEP1}+{PROMPT_VERSION_STEP2}",
        model_name=llm.model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        llm_ms=llm_ms,
        rca_trace=trace,
    )


def _trace_call(t: ToolCallTrace) -> dict[str, Any]:
    return {
        "name": t.name,
        "arguments": t.arguments,
        "citation_id": t.citation_id,
    }


# Test seam.
def _clear_cache() -> None:
    _STEP1_CACHE.clear()
