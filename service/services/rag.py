"""RAG orchestration: retrieve + assemble + LLM + audit."""
from __future__ import annotations

import time
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from db.connection import SessionFactory
from models.schemas import (
    AnchorType,
    BucketExclusion,
    ChatRequest,
    ChatResponse,
    QueryAnchor,
    SourceCitation,
)
from services import retrieval
from services.anchor import is_control_command, resolve_anchor
from services.audit import write_audit, _json_safe
from services.context_assembler import (
    assemble_prompt,
    is_evidence_insufficient,
)
from services.embeddings import embed_one
from services.llm import get_llm_client
from services.prompts import get_active_prompt
from services.response_parser import (
    extract_cited_ids,
    has_any_citations,
    parse_confidence,
)
from services.rules import evaluate_rules
from services.metrics import (
    chat_confidence_total,
    chat_in_flight,
    chat_short_circuit_total,
    chat_total_seconds,
    rca_chain_total,
    record_llm_usage,
    retrieval_latency_seconds,
    retrieval_mode_used,
)


_log = structlog.get_logger(__name__)


INSUFFICIENT_EVIDENCE_TEMPLATE = (
    "I don't have enough evidence to answer this confidently.\n\n"
    "What I checked:\n"
    "- Document corpus: no relevant maintenance, downtime, or quality records found\n"
    "- Recent events (last {hours}h): none\n"
    "- Deterministic rules: none matched current conditions\n"
    "- Approved line memory: nothing relevant\n"
    "- Live plant context: {tag_status}\n\n"
    "You may want to:\n"
    "- Rephrase your question with more specific terms\n"
    "- Ask about a specific tag, event, or piece of equipment\n"
    "- Check if relevant reports have been ingested into the system\n\n"
    "CONFIDENCE: INSUFFICIENT_EVIDENCE"
)


async def _ensure_user_profile(session: AsyncSession, user_id: str) -> dict[str, Any]:
    row = (await session.execute(
        text(
            """
            SELECT id, role_primary, response_detail_level, response_style
            FROM user_profiles WHERE id = :uid
            """
        ),
        {"uid": user_id},
    )).mappings().first()
    if row:
        await session.execute(
            text("UPDATE user_profiles SET last_active_at = NOW() WHERE id = :uid"),
            {"uid": user_id},
        )
        return dict(row)
    # Auto-provision with safe defaults; engineer can edit later.
    await session.execute(
        text(
            """
            INSERT INTO user_profiles (id, display_name, role_primary, last_active_at)
            VALUES (:uid, :uid, 'operator', NOW())
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"uid": user_id},
    )
    return {
        "id": user_id,
        "role_primary": "operator",
        "response_detail_level": "standard",
        "response_style": "balanced",
    }


async def _get_or_create_conversation(
    session: AsyncSession,
    *,
    conversation_id: UUID | None,
    session_id: str,
    user_id: str,
    line_id: str,
) -> UUID:
    if conversation_id:
        existing = (await session.execute(
            text("SELECT id FROM conversations WHERE id = :cid"),
            {"cid": conversation_id},
        )).first()
        if existing:
            return conversation_id
    new_id = uuid4()
    await session.execute(
        text(
            """
            INSERT INTO conversations (id, session_id, user_id, line_id)
            VALUES (:id, :sid, :uid, :line)
            """
        ),
        {"id": new_id, "sid": session_id, "uid": user_id, "line": line_id},
    )
    return new_id


async def _load_recent_history(
    session: AsyncSession, conversation_id: UUID, limit: int = 6
) -> list[tuple[str, str]]:
    rows = (await session.execute(
        text(
            """
            SELECT role, content FROM messages
            WHERE conversation_id = :cid
            ORDER BY created_at DESC LIMIT :lim
            """
        ),
        {"cid": conversation_id, "lim": limit},
    )).mappings().all()
    return [(r["role"], r["content"]) for r in reversed(rows)]


async def _insert_message(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    role: str,
    content: str,
    sources: list[dict[str, Any]] | None = None,
    confidence: str | None = None,
    context_snapshot: dict[str, Any] | None = None,
    prompt_version: str | None = None,
    model_name: str | None = None,
    model_params: dict[str, Any] | None = None,
    token_usage: dict[str, Any] | None = None,
    retrieval_scores: list[dict[str, Any]] | None = None,
    rules_matched: list[dict[str, Any]] | None = None,
    memories_used: list[dict[str, Any]] | None = None,
    latency_ms: int | None = None,
    latency_breakdown: dict[str, Any] | None = None,
) -> UUID:
    msg_id = uuid4()
    await session.execute(
        text(
            """
            INSERT INTO messages (
                id, conversation_id, role, content, sources, confidence_label,
                context_snapshot, prompt_version, model_name, model_params,
                token_usage, retrieval_scores, rules_matched, memories_used,
                latency_ms, latency_breakdown
            ) VALUES (
                :id, :cid, :role, :content, CAST(:sources AS jsonb), :conf,
                CAST(:ctx AS jsonb), :pver, :mname, CAST(:mparams AS jsonb),
                CAST(:tu AS jsonb), CAST(:rs AS jsonb), CAST(:rm AS jsonb),
                CAST(:mu AS jsonb), :lat, CAST(:lb AS jsonb)
            )
            """
        ),
        {
            "id": msg_id,
            "cid": conversation_id,
            "role": role,
            "content": content,
            "sources": _json_safe(sources or []),
            "conf": confidence,
            "ctx": _json_safe(context_snapshot or {}),
            "pver": prompt_version,
            "mname": model_name,
            "mparams": _json_safe(model_params or {}),
            "tu": _json_safe(token_usage or {}),
            "rs": _json_safe(retrieval_scores or []),
            "rm": _json_safe(rules_matched or []),
            "mu": _json_safe(memories_used or []),
            "lat": latency_ms,
            "lb": _json_safe(latency_breakdown or {}),
        },
    )
    await session.execute(
        text(
            """
            UPDATE conversations
            SET message_count = message_count + 1
            WHERE id = :cid
            """
        ),
        {"cid": conversation_id},
    )
    return msg_id


async def _short_circuit_refusal(
    session: AsyncSession,
    conversation_id: UUID,
    req: ChatRequest,
    *,
    reason: str,
    text_: str,
    anchor: QueryAnchor | None,
) -> tuple[UUID, str]:
    """Persist an assistant message that didn't go through the LLM, and audit it."""
    msg_id = await _insert_message(
        session,
        conversation_id=conversation_id,
        role="assistant",
        content=text_,
        sources=[],
        confidence="insufficient_evidence",
        context_snapshot={
            "short_circuit": reason,
            "parsed_anchor": anchor.model_dump(mode="json") if anchor else None,
        },
        prompt_version="(short_circuit)",
        model_name="(none)",
    )
    await write_audit(
        session,
        event_type="chat_query",
        user_id=req.user_id,
        session_id=req.session_id,
        entity_type="message",
        entity_id=str(msg_id),
        details={"short_circuit": reason},
    )
    return msg_id, text_


async def handle_chat(req: ChatRequest) -> ChatResponse:
    """
    Orchestrate a chat query.

    DB-session lifecycle (Sprint 1 / A1):
    - Phase 1 (write+read): persist user message, ensure profile/conversation,
      run retrieval, assemble prompt, handle every short-circuit. Single
      session, committed and closed before phase 2.
    - Phase 2 (no DB session): LLM call. The asyncpg pool is not held
      while the LLM is responding (5-15 s typical).
    - Phase 3 (write): persist the assistant message + audit entry. New
      short-lived session.
    """
    settings = get_settings()
    t0 = time.perf_counter()

    chat_in_flight.inc()
    confidence: str | None = None  # set on the LLM path; remains None for SC + errors
    try:
        # ---------------- Phase 1 — pre-LLM (own session) -----------------
        async with SessionFactory() as session:
            try:
                phase1 = await _phase_pre_llm(session, req, t0)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

        # If phase 1 short-circuited, return immediately.
        if phase1.short_circuit_response is not None:
            # Counters were already incremented inside _phase_pre_llm.
            return phase1.short_circuit_response

        # -------------- Phase 2 — LLM call (NO db session) ---------------
        sys_prompt = phase1.sys_prompt
        assembled = phase1.assembled
        llm = get_llm_client()

        # Sprint 4 / B8 — RCA reasoning chain. Triggered only when the
        # parsed anchor is a past event AND the query has causal intent
        # AND the feature flag is on. Otherwise fall through to one-shot.
        from services.rca import handle_rca, should_use_rca_chain
        rca_outcome = None
        if should_use_rca_chain(req.query, phase1.anchor):
            t_llm = time.perf_counter()
            try:
                rca_outcome = await handle_rca(
                    llm=llm, query=req.query, anchor=phase1.anchor,
                    assembled=assembled,
                )
                llm_ms = int((time.perf_counter() - t_llm) * 1000)
                record_llm_usage(
                    llm.model_name,
                    rca_outcome.prompt_tokens,
                    rca_outcome.completion_tokens,
                )
                response_text = rca_outcome.response_text
                confidence = rca_outcome.confidence
                used_sources = rca_outcome.used_sources
                # Add new (RCA-only) citations into the offered set so
                # they're persisted as part of the snapshot.
                assembled.citations.extend(rca_outcome.new_citations)
                rca_chain_total.labels(
                    outcome="cache_hit_step1" if rca_outcome.rca_trace.cache_hit_step1
                    else "completed"
                ).inc()
            except Exception as e:  # graceful degradation
                _log.warning("rca_chain_failed", err=str(e))
                rca_outcome = None
                rca_chain_total.labels(outcome="failed").inc()
        if rca_outcome is None:
            t_llm = time.perf_counter()
            llm_resp = await llm.complete(sys_prompt.content, assembled.user_block)
            llm_ms = int((time.perf_counter() - t_llm) * 1000)
            record_llm_usage(
                llm.model_name, llm_resp.prompt_tokens, llm_resp.completion_tokens
            )

            # --- Response validation -------------------------------------------
            response_text = llm_resp.content.strip()
            confidence = parse_confidence(response_text)

            # If the LLM produced something but cited nothing, downgrade confidence.
            if not has_any_citations(response_text):
                if confidence == "confirmed":
                    confidence = "hypothesis"
                response_text += (
                    "\n\n[NOTE: The assistant did not include source citations. "
                    "Treat these statements with caution.]"
                )

            cited_ids = extract_cited_ids(response_text)
            used_sources: list[SourceCitation] = [
                c for c in assembled.citations if c.id in cited_ids
            ]

        total_ms = int((time.perf_counter() - t0) * 1000)

        # Token + prompt-version unification across one-shot and RCA paths.
        if rca_outcome is not None:
            tokens_prompt = rca_outcome.prompt_tokens
            tokens_completion = rca_outcome.completion_tokens
            tokens_total = rca_outcome.total_tokens
            effective_prompt_version = rca_outcome.prompt_version
        else:
            tokens_prompt = llm_resp.prompt_tokens
            tokens_completion = llm_resp.completion_tokens
            tokens_total = llm_resp.total_tokens
            effective_prompt_version = sys_prompt.version

        # ---------------- Phase 3 — persist (new session) ---------------------
        async with SessionFactory() as session:
            try:
                snapshot: dict[str, Any] = {
                    "live_context": req.live_context.model_dump(mode="json"),
                    "summary": assembled.summary,
                    "parsed_anchor": phase1.anchor.model_dump(mode="json"),
                    "excluded_buckets": [
                        eb.model_dump(mode="json") for eb in assembled.excluded_buckets
                    ],
                    "matched_history_run_ids": [
                        str(m.run_id) for m in phase1.matched_history
                    ],
                    "work_order_ids": [str(w.wo_id) for w in phase1.work_orders],
                    "camera_clip_handles": [
                        c.storage_handle for c in req.live_context.attached_clips
                        if c.storage_handle
                    ],
                    "all_citations_offered": [
                        c.model_dump(mode="json") for c in assembled.citations
                    ],
                }
                if rca_outcome is not None:
                    snapshot["rca_trace"] = rca_outcome.rca_trace.model_dump(mode="json")

                msg_id = await _insert_message(
                    session,
                    conversation_id=phase1.conversation_id,
                    role="assistant",
                    content=response_text,
                    sources=[s.model_dump(mode="json") for s in used_sources],
                    confidence=confidence,
                    context_snapshot=snapshot,
                    prompt_version=effective_prompt_version,
                    model_name=llm.model_name,
                    model_params={"temperature": settings.openai_temperature},
                    token_usage={
                        "prompt_tokens": tokens_prompt,
                        "completion_tokens": tokens_completion,
                        "total_tokens": tokens_total,
                    },
                    retrieval_scores=[
                        {"chunk_id": str(c.chunk_id), "similarity": c.similarity,
                         "blended": c.blended_score, "title": c.document_title}
                        for c in phase1.chunks
                    ],
                    rules_matched=[
                        {"rule_id": r.rule_id, "rule_name": r.rule_name,
                         "severity": r.severity, "matched": r.matched_conditions}
                        for r in phase1.rules
                    ],
                    memories_used=[
                        {"memory_id": str(m.memory_id), "category": m.category,
                         "similarity": m.similarity}
                        for m in phase1.memories
                    ],
                    latency_ms=total_ms,
                    latency_breakdown={
                        "retrieval_ms": phase1.retrieval_ms,
                        "llm_ms": llm_ms,
                        "total_ms": total_ms,
                    },
                )

                await write_audit(
                    session,
                    event_type="chat_query",
                    user_id=req.user_id,
                    session_id=req.session_id,
                    entity_type="message",
                    entity_id=str(msg_id),
                    details={
                        "summary": assembled.summary,
                        "confidence": confidence,
                        "model": llm.model_name,
                        "prompt_version": effective_prompt_version,
                        "tokens": tokens_total,
                        "rca_chain": rca_outcome is not None,
                    },
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise

        return ChatResponse(
            message_id=msg_id,
            conversation_id=phase1.conversation_id,
            response=response_text,
            sources=used_sources,
            confidence_label=confidence,
            context_summary=assembled.summary,
            processing_time_ms=total_ms,
            prompt_version=effective_prompt_version,
            model_name=llm.model_name,
            rca_summary=(
                {
                    "hypotheses": [
                        {"cause_label": h["cause_label"],
                         "prior_probability": h.get("prior_probability")}
                        for h in rca_outcome.rca_trace.step1.get("hypotheses", [])
                    ],
                    "tool_calls_total": rca_outcome.rca_trace.total_tool_calls,
                    "cache_hit_step1": rca_outcome.rca_trace.cache_hit_step1,
                }
                if rca_outcome is not None else None
            ),
        )

    finally:
        chat_total_seconds.observe(time.perf_counter() - t0)
        # Only increment when the LLM path completed; short-circuits already
        # recorded their own confidence label, errors record nothing.
        if confidence is not None:
            chat_confidence_total.labels(label=confidence).inc()
        chat_in_flight.dec()


# ---------------------------------------------------------------------------
# Phase 1 helper
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field
from services.context_assembler import AssembledPrompt
from services.retrieval import (
    MatchedHistoryRow,
    RetrievedChunk,
    RetrievedEvent,
    RetrievedMemory,
    RetrievedWorkOrder,
)
from services.rules import MatchedRule


@dataclass
class _Phase1Result:
    conversation_id: UUID
    anchor: QueryAnchor
    short_circuit_response: ChatResponse | None = None
    sys_prompt: Any = None
    assembled: AssembledPrompt | None = None
    chunks: list[RetrievedChunk] = field(default_factory=list)
    events: list[RetrievedEvent] = field(default_factory=list)
    memories: list[RetrievedMemory] = field(default_factory=list)
    rules: list[MatchedRule] = field(default_factory=list)
    matched_history: list[MatchedHistoryRow] = field(default_factory=list)
    work_orders: list[RetrievedWorkOrder] = field(default_factory=list)
    retrieval_ms: int = 0


async def _phase_pre_llm(
    session: AsyncSession, req: ChatRequest, t0: float
) -> _Phase1Result:
    settings = get_settings()
    profile = await _ensure_user_profile(session, req.user_id)
    conversation_id = await _get_or_create_conversation(
        session,
        conversation_id=req.conversation_id,
        session_id=req.session_id,
        user_id=req.user_id,
        line_id=req.line_id,
    )

    # Persist the user message immediately (audit-friendly).
    await _insert_message(
        session,
        conversation_id=conversation_id,
        role="user",
        content=req.query,
        context_snapshot={"live_context": req.live_context.model_dump(mode="json")},
    )

    history = await _load_recent_history(session, conversation_id, limit=6)
    history = [(r, c) for r, c in history if c != req.query][-6:]

    anchor: QueryAnchor = req.live_context.anchor or resolve_anchor(req.query)
    req.live_context.anchor = anchor

    # Control-command refusal short-circuit (design §3.8 case b)
    if is_control_command(req.query):
        chat_short_circuit_total.labels(reason="control_command").inc()
        chat_confidence_total.labels(label="insufficient_evidence").inc()
        msg_id, response_text = await _short_circuit_refusal(
            session, conversation_id, req,
            reason="control_command",
            text_=(
                "I'm read-only and advisory — I won't issue control actions or "
                "setpoint changes. I can describe trade-offs of a hypothetical "
                "change if you re-phrase your question.\n\n"
                "CONFIDENCE: INSUFFICIENT_EVIDENCE"
            ),
            anchor=anchor,
        )
        return _Phase1Result(
            conversation_id=conversation_id,
            anchor=anchor,
            short_circuit_response=ChatResponse(
                message_id=msg_id, conversation_id=conversation_id,
                response=response_text, sources=[],
                confidence_label="insufficient_evidence",
                context_summary={"short_circuit": 1, "reason_control_command": 1},
                processing_time_ms=int((time.perf_counter() - t0) * 1000),
                prompt_version="(short_circuit)", model_name="(none)",
            ),
        )

    if anchor.anchor_status != "resolved":
        chat_short_circuit_total.labels(reason="clarification_required").inc()
        chat_confidence_total.labels(label="insufficient_evidence").inc()
        msg_id, response_text = await _short_circuit_refusal(
            session, conversation_id, req,
            reason=anchor.anchor_status,
            text_=anchor.clarification_prompt or (
                "I need a bit more context to answer this. Could you tell me "
                "whether you mean a specific past event, current state, or a "
                "recurring pattern? A date, run number (R-YYYYMMDD-NN), or "
                "sample ID (QR-NNNNN) would help."
            ),
            anchor=anchor,
        )
        return _Phase1Result(
            conversation_id=conversation_id,
            anchor=anchor,
            short_circuit_response=ChatResponse(
                message_id=msg_id, conversation_id=conversation_id,
                response=response_text, sources=[],
                confidence_label="insufficient_evidence",
                context_summary={"clarification_required": 1},
                processing_time_ms=int((time.perf_counter() - t0) * 1000),
                prompt_version="(clarification)", model_name="(none)",
            ),
        )

    # --- Retrieval -------------------------------------------------------
    t_retr = time.perf_counter()
    query_vec = await embed_one(req.query)
    if settings.retrieval_mode == "hybrid":
        chunks = await retrieval.retrieve_chunks_hybrid(
            session,
            query=req.query,
            query_vector=query_vec,
            line_id=req.line_id,
            failure_mode=anchor.failure_mode_scope,
            equipment=anchor.equipment_scope,
        )
        retrieval_mode_used.labels(mode="hybrid").inc()
    else:
        chunks = await retrieval.retrieve_chunks(session, query_vec, req.line_id)
        retrieval_mode_used.labels(mode="vector").inc()

    if anchor.anchor_type == "past_event" and anchor.anchor_time:
        events = await retrieval.retrieve_events_around_anchor(
            session, line_id=req.line_id, anchor_time=anchor.anchor_time,
        )
    else:
        events = await retrieval.retrieve_recent_events(session, req.line_id)

    matched_history: list[MatchedHistoryRow] = []
    if anchor.style_scope and anchor.failure_mode_scope:
        matched_history = await retrieval.retrieve_failure_mode_matched(
            session,
            line_id=req.line_id,
            style=anchor.style_scope,
            failure_mode=anchor.failure_mode_scope,
            before=anchor.anchor_time,
        )

    work_orders = await retrieval.retrieve_work_orders(
        session,
        line_id=req.line_id,
        equipment_scope=anchor.equipment_scope or None,
        before=anchor.anchor_time,
    )

    memories = await retrieval.retrieve_memories(session, query_vec, req.line_id)
    rules = await evaluate_rules(session, req.live_context)
    if memories:
        await retrieval.mark_memories_accessed(
            session, [m.memory_id for m in memories]
        )
    retrieval_ms = int((time.perf_counter() - t_retr) * 1000)
    retrieval_latency_seconds.labels(stage="total").observe(
        (time.perf_counter() - t_retr)
    )

    assembled = assemble_prompt(
        user_query=req.query,
        curated=req.live_context,
        chunks=chunks,
        events=events,
        memories=memories,
        rules=rules,
        matched_history=matched_history,
        work_orders=work_orders,
        conversation_history=history,
        user_role=profile.get("role_primary"),
        response_detail_level=profile.get("response_detail_level", "standard"),
        response_style=profile.get("response_style", "balanced"),
        change_ledger=await _maybe_build_change_ledger(session, req, anchor),
        multivariate_anomaly=await _maybe_score_anomaly(session, req, anchor),
    )

    sys_prompt = await get_active_prompt(session, settings.active_system_prompt_name)

    # --- Insufficient evidence short-circuit ----------------------------
    if is_evidence_insufficient(assembled.summary):
        chat_short_circuit_total.labels(reason="insufficient_evidence").inc()
        chat_confidence_total.labels(label="insufficient_evidence").inc()
        tag_status = (
            "no key tags supplied" if not req.live_context.key_tags
            else f"{len(req.live_context.key_tags)} tags supplied but no matches"
        )
        response_text = INSUFFICIENT_EVIDENCE_TEMPLATE.format(
            hours=settings.retrieval_recent_events_hours,
            tag_status=tag_status,
        )
        total_ms = int((time.perf_counter() - t0) * 1000)
        msg_id = await _insert_message(
            session,
            conversation_id=conversation_id,
            role="assistant",
            content=response_text,
            sources=[],
            confidence="insufficient_evidence",
            context_snapshot={
                "summary": assembled.summary,
                "short_circuit": True,
                "parsed_anchor": anchor.model_dump(mode="json"),
                "excluded_buckets": [
                    eb.model_dump(mode="json") for eb in assembled.excluded_buckets
                ],
            },
            prompt_version=sys_prompt.version,
            model_name="(none - short-circuit)",
            latency_ms=total_ms,
            latency_breakdown={
                "retrieval_ms": retrieval_ms, "llm_ms": 0, "total_ms": total_ms,
            },
        )
        await write_audit(
            session,
            event_type="chat_query",
            user_id=req.user_id,
            session_id=req.session_id,
            entity_type="message",
            entity_id=str(msg_id),
            details={
                "short_circuit": "insufficient_evidence",
                "summary": assembled.summary,
                "anchor_type": anchor.anchor_type,
            },
        )
        return _Phase1Result(
            conversation_id=conversation_id,
            anchor=anchor,
            short_circuit_response=ChatResponse(
                message_id=msg_id,
                conversation_id=conversation_id,
                response=response_text,
                sources=[],
                confidence_label="insufficient_evidence",
                context_summary=assembled.summary,
                processing_time_ms=total_ms,
                prompt_version=sys_prompt.version,
                model_name="(none - short-circuit)",
            ),
        )

    return _Phase1Result(
        conversation_id=conversation_id,
        anchor=anchor,
        sys_prompt=sys_prompt,
        assembled=assembled,
        chunks=chunks,
        events=events,
        memories=memories,
        rules=rules,
        matched_history=matched_history,
        work_orders=work_orders,
        retrieval_ms=retrieval_ms,
    )



# ---------------------------------------------------------------------------
# Sprint 5 helpers � change ledger (B9) and multivariate anomaly (B7)
# ---------------------------------------------------------------------------

from services.baseline_cache import TagBaseline
from services.change_ledger import ChangeLedger, build_change_ledger
from services.anomaly import score_live_snapshot


def _baselines_from_curated(curated) -> dict[str, TagBaseline]:
    """Project the v1 tag_summaries onto the TagBaseline shape."""
    out: dict[str, TagBaseline] = {}
    for s in curated.tag_summaries:
        if s.mean is None:
            continue
        tb = TagBaseline(tag_name=s.name)
        tb.mean = s.mean
        tb.std = s.std if s.std is not None else 0.0
        tb.min = s.min
        tb.max = s.max
        out[s.name] = tb
    # Deviations are even better-grounded baselines if available.
    for d in curated.deviations:
        if d.baseline_mean is None:
            continue
        tb = out.get(d.name) or TagBaseline(tag_name=d.name)
        tb.mean = d.baseline_mean
        if d.baseline_std is not None:
            tb.std = d.baseline_std
        out[d.name] = tb
    return out


def _current_tags_from_curated(curated) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in curated.key_tags:
        if isinstance(t.value, (int, float)) and not isinstance(t.value, bool):
            out[t.name] = float(t.value)
    return out


async def _maybe_build_change_ledger(
    session: AsyncSession, req: ChatRequest, anchor: QueryAnchor
) -> ChangeLedger | None:
    """Build the change ledger when the anchor justifies it. Best-effort:
    returns None on any error so a one-shot RAG path still works."""
    if anchor.anchor_type != "past_event":
        return None
    try:
        recipe = req.live_context.recipe
        return await build_change_ledger(
            session,
            current_tags=_current_tags_from_curated(req.live_context),
            baselines=_baselines_from_curated(req.live_context),
            current_recipe_id=recipe.recipe_id if recipe else None,
            current_target_specs=(recipe.target_specs if recipe else {}) or {},
            current_crew=recipe.crew if recipe else None,
            current_shift=recipe.shift if recipe else None,
            line_id=req.line_id,
            style=anchor.style_scope or (recipe.product_style if recipe else None),
            failure_mode=anchor.failure_mode_scope,
            before=anchor.anchor_time,
            equipment_scope=anchor.equipment_scope or None,
        )
    except Exception as e:  # pragma: no cover � defensive
        _log.warning("change_ledger_build_failed", err=str(e))
        return None


async def _maybe_score_anomaly(
    session: AsyncSession, req: ChatRequest, anchor: QueryAnchor
):
    """Best-effort multivariate anomaly score for current_state queries."""
    settings = get_settings()
    if not settings.anomaly_enabled:
        return None
    if anchor.anchor_type not in ("current_state", "pattern"):
        return None
    recipe = req.live_context.recipe
    if recipe is None or not recipe.product_style:
        return None
    try:
        return await score_live_snapshot(
            session,
            line_id=req.line_id,
            style=recipe.product_style,
            front_step=recipe.front_step,
            current_tags=_current_tags_from_curated(req.live_context),
        )
    except Exception as e:  # pragma: no cover
        _log.warning("anomaly_score_failed", err=str(e))
        return None
