"""Anchor-conditional context assembly (design sections 3.3 / 3.5)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from models.schemas import (
    BucketExclusion,
    CuratedContextPackage,
    QueryAnchor,
    SourceCitation,
    TagBucketEvidence,
)
from services.retrieval import (
    MatchedHistoryRow,
    RetrievedChunk,
    RetrievedEvent,
    RetrievedMemory,
    RetrievedWorkOrder,
)
from services.rules import MatchedRule


@dataclass
class AssembledPrompt:
    user_block: str
    citations: list[SourceCitation] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)
    excluded_buckets: list[BucketExclusion] = field(default_factory=list)


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else "n/a"


def _section(title: str, body: str) -> str:
    return f"=== {title} ===\n{body.strip() or '(none)'}\n"


def _na_section(title: str, reason: str) -> str:
    return f"=== {title} ===\n[NOT APPLICABLE -- {reason}]\n"


def _box_plot_text(samples: list[float], current: float | None) -> str:
    if not samples:
        return ""
    lo, hi = min(samples), max(samples)
    if lo == hi:
        return f"box: [{lo:.1f}]"
    bar = f"----[{lo:.1f}----{hi:.1f}]----"
    if current is None:
        return f"box: [|{bar}|]"
    span = hi - lo
    pos = (current - lo) / span if span else 0.5
    if pos < 0:
        marker = "X far left"
    elif pos > 1:
        marker = "X far right"
    else:
        marker = "X inside box"
    return f"box: [|{bar}|]   current={current}  {marker}"


def _render_anchor_block(anchor: QueryAnchor | None) -> str:
    if anchor is None:
        return _section("PARSED ANCHOR", "(no anchor parsed; v1 assembly)")
    lines = [
        f"anchor_type: {anchor.anchor_type}",
        f"anchor_time: {_fmt_dt(anchor.anchor_time)}",
        f"anchor_event_id: {anchor.anchor_event_id or 'null'}",
        f"anchor_run_id:   {anchor.anchor_run_id or 'null'}",
        f"style_scope:     {anchor.style_scope or 'null'}",
        f"failure_mode_scope: {anchor.failure_mode_scope or 'null'}",
        f"equipment_scope: {anchor.equipment_scope or 'null'}",
        f"anchor_status:   {anchor.anchor_status}",
    ]
    if anchor.clarification_prompt:
        lines.append(f"clarification:   {anchor.clarification_prompt}")
    return _section("PARSED ANCHOR", "\n".join(lines))


def _render_tag_evidence(tag_ev, next_id, citations) -> str:
    if not tag_ev:
        return ""
    lines: list[str] = []
    for t in tag_ev:
        cur = f"current={t.current}" if t.current is not None else ""
        tgt = f" target={t.target}" if t.target is not None else ""
        lines.append(f"{t.name}  {cur}{tgt}  class={t.tag_class}")
        for b in t.baselines:
            cid = next_id()
            mean = "?" if b.mean is None else f"{b.mean:.2f}"
            mn = "?" if b.min is None else f"{b.min:.2f}"
            mx = "?" if b.max is None else f"{b.max:.2f}"
            sd = "?" if b.std is None else f"{b.std:.2f}"
            lines.append(
                f"  [{cid}] {b.bucket}: mean={mean} min={mn} max={mx} std={sd}"
                + (f" -- {b.note}" if b.note else "")
            )
            if b.samples:
                bp = _box_plot_text(list(b.samples), t.current)
                if bp:
                    lines.append(f"        {bp}")
            cite_type = (
                "BASELINE_COMPARE" if b.bucket in (
                    "normal_baseline_14d", "last_4_runs", "pre_anchor_24h",
                ) else "HISTORIAN_STAT"
            )
            citations.append(SourceCitation(
                id=cid, type=cite_type,
                title=f"{t.name} -- {b.bucket}",
                excerpt=f"mean={mean} min={mn} max={mx} std={sd}",
                metadata={
                    "bucket": b.bucket,
                    "tag_class": t.tag_class,
                    "window_start": b.window_start.isoformat() if b.window_start else None,
                    "window_end": b.window_end.isoformat() if b.window_end else None,
                },
            ))
    return "\n".join(lines)


def _render_matched_history(matched, next_id, citations) -> str:
    if not matched:
        return ""
    lines: list[str] = []
    for m in matched:
        cid = next_id()
        rn = m.run_number or str(m.run_id)
        when = _fmt_dt(m.detected_time)
        lines.append(
            f"  [{cid}] run {rn} style={m.product_style or '?'} "
            f"front_step={m.front_step or '?'} mode={m.failure_mode or '?'} "
            f"detected={when}"
        )
        citations.append(SourceCitation(
            id=cid, type="MATCHED_HISTORY",
            title=f"Matched prior run {rn} ({m.failure_mode})",
            excerpt=f"style={m.product_style} mode={m.failure_mode} {when}",
            metadata={"run_id": str(m.run_id), "failure_mode": m.failure_mode},
        ))
    return "\n".join(lines)


def _render_clips(clips, next_id, citations) -> str:
    if not clips:
        return ""
    lines: list[str] = []
    for c in clips:
        cid = next_id()
        loc = c.camera_location or c.camera_id
        when = (
            f"{_fmt_dt(c.clip_start)} -> {_fmt_dt(c.clip_end)}"
            if c.clip_start and c.clip_end else "(no times)"
        )
        status = c.extraction_status or "?"
        lines.append(f"  [{cid}] {loc}  {when}  status={status}")
        citations.append(SourceCitation(
            id=cid, type="CAMERA_CLIP",
            title=f"Symphony clip -- {loc}",
            excerpt=when,
            metadata={
                "clip_id": str(c.clip_id) if c.clip_id else None,
                "event_id": str(c.event_id) if c.event_id else None,
                "storage_handle": c.storage_handle,
            },
        ))
    return "\n".join(lines)


def _render_work_orders(wos, next_id, citations) -> str:
    if not wos:
        return ""
    lines: list[str] = []
    for w in wos:
        cid = next_id()
        opened = _fmt_dt(w.date_opened)
        closed = _fmt_dt(w.date_closed) if w.date_closed else "open"
        lines.append(
            f"  [{cid}] {w.wo_number} eq={w.equipment_id or '?'} "
            f"type={w.wo_type or '?'} opened={opened} closed={closed}\n"
            f"        problem: {(w.problem_description or '')[:200]}\n"
            f"        resolution: {(w.resolution_notes or '')[:200]}"
        )
        citations.append(SourceCitation(
            id=cid, type="WORK_ORDER",
            title=f"WO {w.wo_number} ({w.equipment_id or 'eq?'})",
            excerpt=(w.resolution_notes or w.problem_description or "")[:280],
            metadata={"wo_id": str(w.wo_id), "wo_number": w.wo_number},
        ))
    return "\n".join(lines)


def _render_change_ledger(cl: Any) -> str:
    """Render the B9 ChangeLedger into a compact, LLM-readable block."""
    lines: list[str] = []
    tag_deltas = getattr(cl, "tag_deltas", []) or []
    if tag_deltas:
        lines.append("TAG DELTAS vs. matched-history baseline (sigma-ranked):")
        for d in tag_deltas:
            lines.append(
                f"  - {d.tag_name}: current={d.current_value:.4g} | "
                f"baseline={d.baseline_mean:.4g}±{d.baseline_std:.4g} | "
                f"σ={d.sigma:+.2f} ({d.direction})"
            )
    recipe_deltas = getattr(cl, "recipe_deltas", []) or []
    if recipe_deltas:
        lines.append("")
        lines.append("RECIPE / SETPOINT DELTAS:")
        for d in recipe_deltas:
            lines.append(
                f"  - {d.field}: current={d.current!r} vs. baseline={d.baseline!r}"
                f"   ({d.note})"
            )
    crew_delta = getattr(cl, "crew_delta", None)
    if crew_delta is not None:
        lines.append("")
        lines.append(
            f"CREW / SHIFT DELTA: {crew_delta.note}"
        )
    changeovers = getattr(cl, "equipment_changeovers", []) or []
    if changeovers:
        lines.append("")
        lines.append("EQUIPMENT CHANGEOVERS BETWEEN PRIOR MATCHED RUN AND NOW:")
        for c in changeovers:
            wo = c.wo_number or "(no WO#)"
            when = c.wo_date.isoformat() if c.wo_date else ""
            summ = (c.summary or "")[:160]
            lines.append(f"  - {c.equipment_id} | WO {wo} {when} — {summ}")
    return "\n".join(lines) if lines else "(no change-ledger entries)"


def _render_multivariate_anomaly(an: Any) -> str:
    """Render the B7 AnomalyResult."""
    contributing = ", ".join(getattr(an, "contributing_tags", []) or []) or "n/a"
    return (
        f"Mahalanobis distance: {an.score:.2f}   "
        f"threshold (p95 of training): {an.threshold:.2f}\n"
        f"Training sample size: {an.sample_size}\n"
        f"Top-contributing tags (per-dim |z|): {contributing}\n"
        "Interpretation: the joint state of these tags is unusual relative\n"
        "to the historical distribution for this (style, front_step). No\n"
        "single tag may be flagged as deviant — this is a JOINT signal."
    )


def assemble_prompt(
    *,
    user_query: str,
    curated: CuratedContextPackage,
    chunks: list[RetrievedChunk],
    events: list[RetrievedEvent],
    memories: list[RetrievedMemory],
    rules: list[MatchedRule],
    matched_history: list[MatchedHistoryRow] | None = None,
    work_orders: list[RetrievedWorkOrder] | None = None,
    conversation_history: list[tuple[str, str]] | None = None,
    user_role: str | None = None,
    response_detail_level: str = "standard",
    response_style: str = "balanced",
    change_ledger: Any = None,  # services.change_ledger.ChangeLedger | None
    multivariate_anomaly: Any = None,  # services.anomaly.AnomalyResult | None
) -> AssembledPrompt:
    citations: list[SourceCitation] = []
    excluded: list[BucketExclusion] = list(curated.excluded_buckets or [])
    cite_id = 0

    def next_id() -> str:
        nonlocal cite_id
        cite_id += 1
        return str(cite_id)

    matched_history = matched_history or []
    work_orders = work_orders or []
    anchor = curated.anchor
    is_past = anchor is not None and anchor.anchor_type == "past_event"
    is_current = anchor is None or anchor.anchor_type == "current_state"
    is_pattern = anchor is not None and anchor.anchor_type == "pattern"

    blocks: list[str] = [_render_anchor_block(anchor)]

    if is_past:
        blocks.append(_na_section(
            "LIVE TAG VALUES", "past-event query -- live state excluded",
        ))
        excluded.append(BucketExclusion(
            bucket="live_tags",
            reason="anchor_type=past_event excludes current state",
        ))
    elif is_pattern:
        blocks.append(_na_section(
            "LIVE TAG VALUES", "pattern query -- using historical aggregates",
        ))
        excluded.append(BucketExclusion(
            bucket="live_tags",
            reason="anchor_type=pattern uses historical aggregates",
        ))
    else:
        live_lines: list[str] = [
            f"Snapshot time: {_fmt_dt(curated.snapshot_time)}",
            f"Line: {curated.line_id}",
        ]
        if curated.recipe:
            rec = curated.recipe
            fs = f" front_step={rec.front_step}" if rec.front_step is not None else ""
            live_lines.append(
                f"Recipe: product_style={rec.product_style or 'n/a'} "
                f"product_family={rec.product_family or 'n/a'} "
                f"recipe_id={rec.recipe_id or 'n/a'}{fs}"
            )
            if rec.target_specs:
                live_lines.append(f"Target specs: {rec.target_specs}")
        if curated.key_tags:
            live_lines.append("\nKey tag values (LIVE_TAG):")
            for t in curated.key_tags:
                cid = next_id()
                target_str = f" target={t.target}" if t.target is not None else ""
                unit_str = f" {t.unit}" if t.unit else ""
                live_lines.append(
                    f"  [{cid}] {t.name} = {t.value}{unit_str}{target_str} "
                    f"(quality={t.quality or 'good'})"
                )
                citations.append(SourceCitation(
                    id=cid, type="LIVE_TAG", title=t.name,
                    excerpt=f"{t.value}{unit_str}",
                    metadata={"target": t.target, "quality": t.quality},
                ))
        if curated.tag_summaries:
            live_lines.append(
                f"\nRecent tag summaries (HISTORIAN_STAT, "
                f"window={curated.historian_window_minutes} min):"
            )
            for s in curated.tag_summaries:
                cid = next_id()
                live_lines.append(
                    f"  [{cid}] {s.name}: mean={s.mean} min={s.min} max={s.max} "
                    f"std={s.std} current={s.current} trend={s.trend}"
                )
                citations.append(SourceCitation(
                    id=cid, type="HISTORIAN_STAT",
                    title=f"{s.name} (last {s.window_minutes}m)",
                    excerpt=f"mean={s.mean} min={s.min} max={s.max} current={s.current}",
                    metadata={"window_minutes": s.window_minutes},
                ))
        if curated.deviations:
            live_lines.append("\nNotable deviations (DEVIATION):")
            for d in curated.deviations:
                cid = next_id()
                sigma = f"{d.sigma_deviation:.1f}sd" if d.sigma_deviation is not None else ""
                pct = f"{d.pct_deviation:.1f}%" if d.pct_deviation is not None else ""
                live_lines.append(
                    f"  [{cid}] {d.name} {d.direction or ''} baseline "
                    f"({sigma} {pct}) current={d.current} baseline_mean={d.baseline_mean}"
                    + (f" - {d.note}" if d.note else "")
                )
                citations.append(SourceCitation(
                    id=cid, type="DEVIATION", title=f"{d.name} deviation",
                    excerpt=f"{d.direction} baseline by {sigma} {pct}".strip(),
                    metadata={"sigma": d.sigma_deviation, "pct": d.pct_deviation},
                ))
        blocks.append(_section("LIVE TAG VALUES", "\n".join(live_lines)))

    if curated.tag_evidence:
        body = _render_tag_evidence(curated.tag_evidence, next_id, citations)
        blocks.append(_section("TAG EVIDENCE BY BUCKET", body))

    if is_past or is_pattern:
        blocks.append(_na_section(
            "LIVE ALARMS",
            "past-event query -- live alarms excluded" if is_past
            else "pattern query -- live alarms excluded",
        ))
        excluded.append(BucketExclusion(
            bucket="live_alarms",
            reason=("past_event" if is_past else "pattern") + " -- live alarms excluded",
        ))
    elif curated.active_alarms:
        alarm_lines: list[str] = []
        for a in curated.active_alarms:
            cid = next_id()
            since = f" since {_fmt_dt(a.active_since)}" if a.active_since else ""
            alarm_lines.append(
                f"  [{cid}] [{a.priority}] {a.display_path or a.source} "
                f"state={a.state}{since}"
                + (f" label={a.label}" if a.label else "")
            )
            citations.append(SourceCitation(
                id=cid, type="ALARM",
                title=f"{a.priority} alarm: {a.display_path or a.source}",
                excerpt=f"state={a.state}",
                metadata={"source": a.source, "priority": a.priority},
            ))
        blocks.append(_section("LIVE ALARMS", "\n".join(alarm_lines)))
    else:
        blocks.append(_section("LIVE ALARMS", ""))

    event_lines: list[str] = []
    title_map = {
        "downtime_events": "Downtime event",
        "quality_results": "Quality result",
        "defect_events": "Defect event",
    }
    for e in events:
        cid = next_id()
        event_lines.append(f"[{cid}] {e.summary}")
        citations.append(SourceCitation(
            id=cid, type="EVENT",
            title=f"{title_map.get(e.event_table, 'Event')} {_fmt_dt(e.event_time)}",
            excerpt=e.summary[:280],
            metadata={"event_id": str(e.event_id), "table": e.event_table},
        ))
    events_title = (
        "RECENT EVENTS (scoped to anchor +/- 72h)" if is_past
        else "RECENT EVENTS (last 72h)" if is_current
        else "EVENTS (pattern scope)"
    )
    blocks.append(_section(events_title, "\n".join(event_lines)))

    if matched_history:
        body = _render_matched_history(matched_history, next_id, citations)
        blocks.append(_section("FAILURE-MODE-MATCHED HISTORY", body))
    elif anchor and anchor.failure_mode_scope and anchor.style_scope:
        blocks.append(_section(
            "FAILURE-MODE-MATCHED HISTORY",
            f"(no prior runs found for style={anchor.style_scope} "
            f"failure_mode={anchor.failure_mode_scope})",
        ))

    doc_lines: list[str] = []
    for c in chunks:
        cid = next_id()
        title = c.document_title or "Untitled"
        date_str = _fmt_dt(c.document_date)
        doc_lines.append(
            f"[{cid}] (sim={c.similarity:.2f}, weight={c.document_weight:.2f}, "
            f"role={c.document_role or c.source_type}) "
            f"{title} | {date_str}\n"
            f"    {c.chunk_text.strip()[:600]}"
        )
        citations.append(SourceCitation(
            id=cid, type="DOCUMENT",
            title=f"{c.source_type}: {title} ({date_str})",
            excerpt=c.chunk_text.strip()[:400],
            score=c.similarity,
            metadata={
                "chunk_id": str(c.chunk_id),
                "document_id": str(c.document_id),
                "source_type": c.source_type,
                "document_role": c.document_role,
                "document_weight": c.document_weight,
            },
        ))
    blocks.append(_section(
        "RETRIEVED DOCUMENTS (weighted by document_role)",
        "\n\n".join(doc_lines),
    ))

    wo_body = _render_work_orders(work_orders, next_id, citations)
    blocks.append(_section("WORK ORDERS (in scope)", wo_body))

    clip_body = _render_clips(curated.attached_clips, next_id, citations)
    blocks.append(_section(
        "CAMERA CLIPS (attached to events in scope)", clip_body,
    ))

    rule_lines: list[str] = []
    for r in rules:
        cid = next_id()
        rule_lines.append(
            f"[{cid}] [{r.severity.upper()}] {r.rule_name}\n"
            f"    Conditions met: {'; '.join(r.matched_conditions)}\n"
            f"    Conclusion: {r.conclusion}"
        )
        citations.append(SourceCitation(
            id=cid, type="RULE",
            title=f"Rule: {r.rule_name}",
            excerpt=r.conclusion,
            metadata={"severity": r.severity, "category": r.category},
        ))
    blocks.append(_section("DETERMINISTIC RULES (matched)", "\n".join(rule_lines)))

    mem_lines: list[str] = []
    for m in memories:
        cid = next_id()
        mem_lines.append(
            f"[{cid}] ({m.category}, confidence={m.confidence}, "
            f"sim={m.similarity:.2f}) {m.content}"
        )
        citations.append(SourceCitation(
            id=cid, type="MEMORY",
            title=f"Approved memory: {m.category}",
            excerpt=m.content[:280],
            score=m.similarity,
            metadata={"memory_id": str(m.memory_id), "confidence": m.confidence},
        ))
    blocks.append(_section("APPROVED LINE MEMORY (relevant)", "\n".join(mem_lines)))

    blocks.append(_section("ML PREDICTIONS", "(no ML models active in this phase)"))

    convo_lines: list[str] = []
    for role, content in (conversation_history or [])[-6:]:
        convo_lines.append(f"{role.upper()}: {content[:500]}")
    blocks.append(_section("CONVERSATION HISTORY (recent)", "\n".join(convo_lines)))

    profile_lines = [
        f"User role: {user_role or 'unknown'}",
        f"Preferred detail level: {response_detail_level}",
        f"Preferred response style: {response_style}",
    ]
    blocks.append(_section(
        "USER PROFILE HINTS (presentation only - facts unchanged)",
        "\n".join(profile_lines),
    ))

    # ---- B9 — Change ledger (auto-computed deltas vs. matched history) ----
    if change_ledger is not None and not getattr(change_ledger, "is_empty", True):
        blocks.append(_section(
            "CHANGE LEDGER (auto-computed; treat as leading hypotheses)",
            _render_change_ledger(change_ledger),
        ))

    # ---- B7 — Multivariate anomaly score (joint state vs. cluster) -------
    if multivariate_anomaly is not None and getattr(multivariate_anomaly, "is_anomaly", False):
        blocks.append(_section(
            "MULTIVARIATE ANOMALY (joint tag state)",
            _render_multivariate_anomaly(multivariate_anomaly),
        ))

    blocks.append(_section("USER QUESTION", user_query.strip()))

    user_block = "\n".join(blocks)

    summary = {
        "key_tags": 0 if is_past or is_pattern else len(curated.key_tags),
        "tag_summaries": 0 if is_past or is_pattern else len(curated.tag_summaries),
        "deviations": 0 if is_past or is_pattern else len(curated.deviations),
        "active_alarms": 0 if is_past or is_pattern else len(curated.active_alarms),
        "tag_evidence_buckets": sum(len(t.baselines) for t in curated.tag_evidence),
        "events": len(events),
        "documents": len(chunks),
        "rules_matched": len(rules),
        "memories_used": len(memories),
        "matched_history": len(matched_history),
        "work_orders": len(work_orders),
        "camera_clips": len(curated.attached_clips),
        "change_ledger_entries": (
            (
                len(getattr(change_ledger, "tag_deltas", []) or [])
                + len(getattr(change_ledger, "recipe_deltas", []) or [])
                + (1 if getattr(change_ledger, "crew_delta", None) else 0)
                + len(getattr(change_ledger, "equipment_changeovers", []) or [])
            )
            if change_ledger is not None else 0
        ),
        "multivariate_anomaly": (
            1 if multivariate_anomaly is not None
            and getattr(multivariate_anomaly, "is_anomaly", False)
            else 0
        ),
        "total_citations": len(citations),
    }

    return AssembledPrompt(
        user_block=user_block,
        citations=citations,
        summary=summary,
        excluded_buckets=excluded,
    )


def is_evidence_insufficient(summary: dict[str, int]) -> bool:
    """True when there is essentially nothing to ground a response on."""
    fields = (
        "documents", "events", "rules_matched", "memories_used",
        "key_tags", "active_alarms", "tag_evidence_buckets",
        "matched_history", "work_orders",
    )
    return not any(summary.get(f, 0) > 0 for f in fields)
