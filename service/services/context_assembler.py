"""
Assembles the curated context package + retrieval results + rules + memories
into the structured prompt sections sent to the LLM.

Critical design rule: the LLM only ever sees clearly-delimited, pre-digested
sections. No raw historian dumps. Each piece of evidence has a citation id
the LLM is required to reference.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from models.schemas import (
    CuratedContextPackage,
    SourceCitation,
)
from services.retrieval import RetrievedChunk, RetrievedEvent, RetrievedMemory
from services.rules import MatchedRule


@dataclass
class AssembledPrompt:
    user_block: str
    citations: list[SourceCitation] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else "n/a"


def _section(title: str, body: str) -> str:
    return f"=== {title} ===\n{body.strip() or '(none)'}\n"


def assemble_prompt(
    *,
    user_query: str,
    curated: CuratedContextPackage,
    chunks: list[RetrievedChunk],
    events: list[RetrievedEvent],
    memories: list[RetrievedMemory],
    rules: list[MatchedRule],
    conversation_history: list[tuple[str, str]] | None = None,
    user_role: str | None = None,
    response_detail_level: str = "standard",
    response_style: str = "balanced",
) -> AssembledPrompt:
    citations: list[SourceCitation] = []
    cite_id = 0

    def next_id() -> str:
        nonlocal cite_id
        cite_id += 1
        return str(cite_id)

    # --- LIVE PLANT CONTEXT ----------------------------------------------
    live_lines: list[str] = [
        f"Snapshot time: {_fmt_dt(curated.snapshot_time)}",
        f"Line: {curated.line_id}",
    ]
    if curated.recipe:
        rec = curated.recipe
        live_lines.append(
            f"Recipe: product_style={rec.product_style or 'n/a'} "
            f"product_family={rec.product_family or 'n/a'} "
            f"recipe_id={rec.recipe_id or 'n/a'}"
        )
        if rec.target_specs:
            live_lines.append(f"Target specs: {rec.target_specs}")

    if curated.key_tags:
        live_lines.append("\nKey tag values:")
        for t in curated.key_tags:
            cid = next_id()
            target_str = f" target={t.target}" if t.target is not None else ""
            unit_str = f" {t.unit}" if t.unit else ""
            live_lines.append(
                f"  [{cid}] {t.name} = {t.value}{unit_str}{target_str} "
                f"(quality={t.quality or 'good'})"
            )
            citations.append(SourceCitation(
                id=cid, type="live_tag", title=t.name,
                excerpt=f"{t.value}{unit_str}",
                metadata={"target": t.target, "quality": t.quality},
            ))

    if curated.tag_summaries:
        live_lines.append(
            f"\nRecent tag summaries (window={curated.historian_window_minutes} min):"
        )
        for s in curated.tag_summaries:
            cid = next_id()
            live_lines.append(
                f"  [{cid}] {s.name}: mean={s.mean} min={s.min} max={s.max} "
                f"std={s.std} current={s.current} trend={s.trend}"
            )
            citations.append(SourceCitation(
                id=cid, type="tag_summary", title=f"{s.name} (last {s.window_minutes}m)",
                excerpt=f"mean={s.mean} min={s.min} max={s.max} current={s.current}",
                metadata={"window_minutes": s.window_minutes},
            ))

    if curated.deviations:
        live_lines.append("\nNotable deviations from baseline:")
        for d in curated.deviations:
            cid = next_id()
            sigma = f"{d.sigma_deviation:.1f}σ" if d.sigma_deviation is not None else ""
            pct = f"{d.pct_deviation:.1f}%" if d.pct_deviation is not None else ""
            live_lines.append(
                f"  [{cid}] {d.name} {d.direction or ''} baseline "
                f"({sigma} {pct}) current={d.current} baseline_mean={d.baseline_mean}"
                + (f" - {d.note}" if d.note else "")
            )
            citations.append(SourceCitation(
                id=cid, type="tag_deviation", title=f"{d.name} deviation",
                excerpt=f"{d.direction} baseline by {sigma} {pct}".strip(),
                metadata={"sigma": d.sigma_deviation, "pct": d.pct_deviation},
            ))

    if curated.active_alarms:
        live_lines.append("\nActive alarms:")
        for a in curated.active_alarms:
            cid = next_id()
            since = f" since {_fmt_dt(a.active_since)}" if a.active_since else ""
            live_lines.append(
                f"  [{cid}] [{a.priority}] {a.display_path or a.source} "
                f"state={a.state}{since}"
                + (f" label={a.label}" if a.label else "")
            )
            citations.append(SourceCitation(
                id=cid, type="active_alarm",
                title=f"{a.priority} alarm: {a.display_path or a.source}",
                excerpt=f"state={a.state}",
                metadata={"source": a.source, "priority": a.priority},
            ))

    live_block = _section("LIVE PLANT CONTEXT", "\n".join(live_lines))

    # --- RECENT EVENTS ---------------------------------------------------
    event_lines: list[str] = []
    for e in events:
        cid = next_id()
        event_lines.append(f"[{cid}] {e.summary}")
        title_map = {
            "downtime_events": "Downtime event",
            "quality_results": "Quality result",
            "defect_events": "Defect event",
        }
        type_map = {
            "downtime_events": "downtime_event",
            "quality_results": "quality_result",
            "defect_events": "defect_event",
        }
        citations.append(SourceCitation(
            id=cid,
            type=type_map[e.event_table],  # type: ignore[arg-type]
            title=f"{title_map[e.event_table]} {_fmt_dt(e.event_time)}",
            excerpt=e.summary[:280],
            metadata={"event_id": str(e.event_id), "table": e.event_table},
        ))
    events_block = _section("RECENT EVENTS (structured)", "\n".join(event_lines))

    # --- RETRIEVED DOCUMENTS ---------------------------------------------
    doc_lines: list[str] = []
    for c in chunks:
        cid = next_id()
        title = c.document_title or "Untitled"
        date_str = _fmt_dt(c.document_date)
        doc_lines.append(
            f"[{cid}] (similarity={c.similarity:.2f}) "
            f"{c.source_type} | {title} | {date_str}\n"
            f"    {c.chunk_text.strip()[:600]}"
        )
        citations.append(SourceCitation(
            id=cid, type="document_chunk",
            title=f"{c.source_type}: {title} ({date_str})",
            excerpt=c.chunk_text.strip()[:400],
            score=c.similarity,
            metadata={
                "chunk_id": str(c.chunk_id),
                "document_id": str(c.document_id),
                "source_type": c.source_type,
            },
        ))
    docs_block = _section("RETRIEVED DOCUMENTS (ranked by relevance)", "\n\n".join(doc_lines))

    # --- DETERMINISTIC RULES ---------------------------------------------
    rule_lines: list[str] = []
    for r in rules:
        cid = next_id()
        rule_lines.append(
            f"[{cid}] [{r.severity.upper()}] {r.rule_name}\n"
            f"    Conditions met: {'; '.join(r.matched_conditions)}\n"
            f"    Conclusion: {r.conclusion}"
        )
        citations.append(SourceCitation(
            id=cid, type="business_rule",
            title=f"Rule: {r.rule_name}",
            excerpt=r.conclusion,
            metadata={"severity": r.severity, "category": r.category},
        ))
    rules_block = _section("DETERMINISTIC RULES (matched)", "\n".join(rule_lines))

    # --- APPROVED LINE MEMORY --------------------------------------------
    mem_lines: list[str] = []
    for m in memories:
        cid = next_id()
        mem_lines.append(
            f"[{cid}] ({m.category}, confidence={m.confidence}, "
            f"similarity={m.similarity:.2f}) {m.content}"
        )
        citations.append(SourceCitation(
            id=cid, type="line_memory",
            title=f"Approved memory: {m.category}",
            excerpt=m.content[:280],
            score=m.similarity,
            metadata={"memory_id": str(m.memory_id), "confidence": m.confidence},
        ))
    memory_block = _section("APPROVED LINE MEMORY (relevant)", "\n".join(mem_lines))

    # --- ML PREDICTIONS (placeholder until Phase 4) ----------------------
    ml_block = _section("ML PREDICTIONS", "(no ML models active in this phase)")

    # --- CONVERSATION HISTORY --------------------------------------------
    convo_lines: list[str] = []
    for role, content in (conversation_history or [])[-6:]:
        convo_lines.append(f"{role.upper()}: {content[:500]}")
    convo_block = _section("CONVERSATION HISTORY (recent)", "\n".join(convo_lines))

    # --- USER PROFILE HINTS ----------------------------------------------
    profile_lines = [
        f"User role: {user_role or 'unknown'}",
        f"Preferred detail level: {response_detail_level}",
        f"Preferred response style: {response_style}",
    ]
    profile_block = _section("USER PROFILE HINTS (presentation only - facts unchanged)",
                             "\n".join(profile_lines))

    # --- USER QUESTION ---------------------------------------------------
    question_block = _section("USER QUESTION", user_query.strip())

    user_block = "\n".join([
        live_block, events_block, docs_block, rules_block,
        memory_block, ml_block, convo_block, profile_block, question_block,
    ])

    summary = {
        "key_tags": len(curated.key_tags),
        "tag_summaries": len(curated.tag_summaries),
        "deviations": len(curated.deviations),
        "active_alarms": len(curated.active_alarms),
        "events": len(events),
        "documents": len(chunks),
        "rules_matched": len(rules),
        "memories_used": len(memories),
        "total_citations": len(citations),
    }

    return AssembledPrompt(user_block=user_block, citations=citations, summary=summary)


def is_evidence_insufficient(summary: dict[str, int]) -> bool:
    """True when there is essentially nothing to ground a response on."""
    has_docs = summary.get("documents", 0) > 0
    has_events = summary.get("events", 0) > 0
    has_rules = summary.get("rules_matched", 0) > 0
    has_memory = summary.get("memories_used", 0) > 0
    has_live = summary.get("key_tags", 0) > 0 or summary.get("active_alarms", 0) > 0
    return not (has_docs or has_events or has_rules or has_memory or has_live)
