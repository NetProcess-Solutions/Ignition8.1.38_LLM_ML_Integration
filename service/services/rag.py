"""RAG orchestration: retrieve + assemble + LLM + audit."""
from __future__ import annotations

import time
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from models.schemas import ChatRequest, ChatResponse, SourceCitation
from services import retrieval
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
                id, conversation_id, role, content, sources, confidence,
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


async def handle_chat(session: AsyncSession, req: ChatRequest) -> ChatResponse:
    settings = get_settings()
    t0 = time.perf_counter()

    profile = await _ensure_user_profile(session, req.user_id)
    conversation_id = await _get_or_create_conversation(
        session,
        conversation_id=req.conversation_id,
        session_id=req.session_id,
        user_id=req.user_id,
        line_id=req.line_id,
    )

    # Persist the user message immediately (audit-friendly).
    user_msg_id = await _insert_message(
        session,
        conversation_id=conversation_id,
        role="user",
        content=req.query,
        context_snapshot={"live_context": req.live_context.model_dump(mode="json")},
    )

    history = await _load_recent_history(session, conversation_id, limit=6)
    # Drop the message we just inserted from the history we send to the LLM
    history = [(r, c) for r, c in history if c != req.query][-6:]

    # --- Retrieval -------------------------------------------------------
    t_retr = time.perf_counter()
    query_vec = await embed_one(req.query)
    chunks = await retrieval.retrieve_chunks(session, query_vec, req.line_id)
    events = await retrieval.retrieve_recent_events(session, req.line_id)
    memories = await retrieval.retrieve_memories(session, query_vec, req.line_id)
    rules = await evaluate_rules(session, req.live_context)
    if memories:
        await retrieval.mark_memories_accessed(session, [m.memory_id for m in memories])
    retrieval_ms = int((time.perf_counter() - t_retr) * 1000)

    # --- Assembly --------------------------------------------------------
    assembled = assemble_prompt(
        user_query=req.query,
        curated=req.live_context,
        chunks=chunks,
        events=events,
        memories=memories,
        rules=rules,
        conversation_history=history,
        user_role=profile.get("role_primary"),
        response_detail_level=profile.get("response_detail_level", "standard"),
        response_style=profile.get("response_style", "balanced"),
    )

    sys_prompt = await get_active_prompt(session, settings.active_system_prompt_name)

    # --- Insufficient evidence short-circuit ----------------------------
    if is_evidence_insufficient(assembled.summary):
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
            context_snapshot={"summary": assembled.summary, "short_circuit": True},
            prompt_version=sys_prompt.version,
            model_name="(none - short-circuit)",
            latency_ms=total_ms,
            latency_breakdown={"retrieval_ms": retrieval_ms, "llm_ms": 0, "total_ms": total_ms},
        )
        await write_audit(
            session,
            event_type="chat_query",
            user_id=req.user_id,
            session_id=req.session_id,
            entity_type="message",
            entity_id=str(msg_id),
            details={"short_circuit": "insufficient_evidence", "summary": assembled.summary},
        )
        return ChatResponse(
            message_id=msg_id,
            conversation_id=conversation_id,
            response=response_text,
            sources=[],
            confidence="insufficient_evidence",
            context_summary=assembled.summary,
            processing_time_ms=total_ms,
            prompt_version=sys_prompt.version,
            model_name="(none - short-circuit)",
        )

    # --- LLM call --------------------------------------------------------
    llm = get_llm_client()
    t_llm = time.perf_counter()
    llm_resp = await llm.complete(sys_prompt.content, assembled.user_block)
    llm_ms = int((time.perf_counter() - t_llm) * 1000)

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

    # Filter citations to only those the LLM actually referenced
    cited_ids = extract_cited_ids(response_text)
    used_sources: list[SourceCitation] = [
        c for c in assembled.citations if c.id in cited_ids
    ]

    total_ms = int((time.perf_counter() - t0) * 1000)

    msg_id = await _insert_message(
        session,
        conversation_id=conversation_id,
        role="assistant",
        content=response_text,
        sources=[s.model_dump(mode="json") for s in used_sources],
        confidence=confidence,
        context_snapshot={
            "live_context": req.live_context.model_dump(mode="json"),
            "summary": assembled.summary,
            "all_citations_offered": [c.model_dump(mode="json") for c in assembled.citations],
        },
        prompt_version=sys_prompt.version,
        model_name=llm.model_name,
        model_params={"temperature": settings.openai_temperature},
        token_usage={
            "prompt_tokens": llm_resp.prompt_tokens,
            "completion_tokens": llm_resp.completion_tokens,
            "total_tokens": llm_resp.total_tokens,
        },
        retrieval_scores=[
            {"chunk_id": str(c.chunk_id), "similarity": c.similarity,
             "blended": c.blended_score, "title": c.document_title}
            for c in chunks
        ],
        rules_matched=[
            {"rule_id": r.rule_id, "rule_name": r.rule_name,
             "severity": r.severity, "matched": r.matched_conditions}
            for r in rules
        ],
        memories_used=[
            {"memory_id": str(m.memory_id), "category": m.category,
             "similarity": m.similarity}
            for m in memories
        ],
        latency_ms=total_ms,
        latency_breakdown={
            "retrieval_ms": retrieval_ms,
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
            "prompt_version": sys_prompt.version,
            "tokens": llm_resp.total_tokens,
        },
    )

    return ChatResponse(
        message_id=msg_id,
        conversation_id=conversation_id,
        response=response_text,
        sources=used_sources,
        confidence=confidence,
        context_summary=assembled.summary,
        processing_time_ms=total_ms,
        prompt_version=sys_prompt.version,
        model_name=llm.model_name,
    )
