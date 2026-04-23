"""Vector + structured retrieval against pgvector and event tables."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings


@dataclass
class RetrievedChunk:
    chunk_id: UUID
    document_id: UUID
    chunk_text: str
    similarity: float
    quality_signal: float
    blended_score: float
    document_title: str | None
    document_date: datetime | None
    source_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievedEvent:
    event_id: UUID
    event_table: str   # 'downtime_events' | 'quality_results' | 'defect_events'
    summary: str
    event_time: datetime
    raw: dict[str, Any]


@dataclass
class RetrievedMemory:
    memory_id: UUID
    category: str
    content: str
    confidence: str
    similarity: float


def _vector_literal(vec: list[float]) -> str:
    """pgvector text literal: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


async def retrieve_chunks(
    session: AsyncSession,
    query_vector: list[float],
    line_id: str,
    top_k: int | None = None,
    min_score: float | None = None,
) -> list[RetrievedChunk]:
    s = get_settings()
    k = top_k or s.retrieval_top_k
    floor = min_score if min_score is not None else s.retrieval_min_score

    sql = text(
        """
        SELECT
            c.id AS chunk_id,
            c.document_id,
            c.chunk_text,
            1 - (c.embedding <=> CAST(:qvec AS vector)) AS similarity,
            COALESCE(q.quality_score, 0.0) AS quality_signal,
            d.title,
            d.document_date,
            d.source_type,
            d.metadata AS doc_metadata,
            c.metadata AS chunk_metadata
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id
        LEFT JOIN chunk_quality_signals q ON q.chunk_id = c.id
        WHERE d.is_active = TRUE
          AND d.line_id = :line_id
          AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> CAST(:qvec AS vector) ASC
        LIMIT :limit
        """
    )

    rows = (await session.execute(
        sql,
        {"qvec": _vector_literal(query_vector), "line_id": line_id, "limit": k * 2},
    )).mappings().all()

    out: list[RetrievedChunk] = []
    for r in rows:
        sim = float(r["similarity"])
        if sim < floor:
            continue
        # Bounded blending: quality contributes at most ±30% of similarity
        quality = float(r["quality_signal"])
        blended = sim + max(min(quality * 0.3, 0.3 * sim), -0.3 * sim)
        out.append(RetrievedChunk(
            chunk_id=r["chunk_id"],
            document_id=r["document_id"],
            chunk_text=r["chunk_text"],
            similarity=sim,
            quality_signal=quality,
            blended_score=blended,
            document_title=r["title"],
            document_date=r["document_date"],
            source_type=r["source_type"],
            metadata={"doc": dict(r["doc_metadata"] or {}), "chunk": dict(r["chunk_metadata"] or {})},
        ))

    out.sort(key=lambda c: c.blended_score, reverse=True)
    return out[:k]


async def retrieve_recent_events(
    session: AsyncSession,
    line_id: str,
    hours: int | None = None,
) -> list[RetrievedEvent]:
    s = get_settings()
    h = hours or s.retrieval_recent_events_hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=h)

    events: list[RetrievedEvent] = []

    downtime = (await session.execute(
        text(
            """
            SELECT id, start_time, end_time, category, subcategory,
                   equipment_id, description, root_cause
            FROM downtime_events
            WHERE line_id = :line_id AND start_time >= :cutoff
            ORDER BY start_time DESC
            LIMIT 20
            """
        ),
        {"line_id": line_id, "cutoff": cutoff},
    )).mappings().all()
    for e in downtime:
        dur = ""
        if e["end_time"]:
            dur = f" ({(e['end_time'] - e['start_time']).total_seconds()/60:.0f} min)"
        events.append(RetrievedEvent(
            event_id=e["id"],
            event_table="downtime_events",
            summary=(
                f"Downtime {e['start_time']:%Y-%m-%d %H:%M} {e['category'] or ''}"
                f"/{e['subcategory'] or ''}{dur}: {e['description'] or ''}"
                + (f" | Root cause: {e['root_cause']}" if e["root_cause"] else "")
            ),
            event_time=e["start_time"],
            raw=dict(e),
        ))

    quality = (await session.execute(
        text(
            """
            SELECT id, test_time, test_type, result, sample_id, notes
            FROM quality_results
            WHERE line_id = :line_id AND test_time >= :cutoff
            ORDER BY test_time DESC
            LIMIT 20
            """
        ),
        {"line_id": line_id, "cutoff": cutoff},
    )).mappings().all()
    for q in quality:
        events.append(RetrievedEvent(
            event_id=q["id"],
            event_table="quality_results",
            summary=(
                f"Quality test {q['test_time']:%Y-%m-%d %H:%M} "
                f"{q['test_type']}: {q['result'].upper()}"
                + (f" - {q['notes']}" if q["notes"] else "")
            ),
            event_time=q["test_time"],
            raw=dict(q),
        ))

    defects = (await session.execute(
        text(
            """
            SELECT id, detected_time, defect_type, severity,
                   description, root_cause, status
            FROM defect_events
            WHERE line_id = :line_id AND detected_time >= :cutoff
            ORDER BY detected_time DESC
            LIMIT 20
            """
        ),
        {"line_id": line_id, "cutoff": cutoff},
    )).mappings().all()
    for d in defects:
        events.append(RetrievedEvent(
            event_id=d["id"],
            event_table="defect_events",
            summary=(
                f"Defect {d['detected_time']:%Y-%m-%d %H:%M} {d['defect_type']} "
                f"[{d['severity'] or 'unspec'}] status={d['status']}: "
                f"{d['description'] or ''}"
                + (f" | Root cause: {d['root_cause']}" if d["root_cause"] else "")
            ),
            event_time=d["detected_time"],
            raw=dict(d),
        ))

    events.sort(key=lambda e: e.event_time, reverse=True)
    return events


async def retrieve_memories(
    session: AsyncSession,
    query_vector: list[float],
    line_id: str,
    top_k: int | None = None,
) -> list[RetrievedMemory]:
    s = get_settings()
    k = top_k or s.memory_top_k

    rows = (await session.execute(
        text(
            """
            SELECT id, category, content, confidence,
                   1 - (embedding <=> CAST(:qvec AS vector)) AS similarity
            FROM line_memory
            WHERE line_id = :line_id
              AND status IN ('approved','reviewed')
              AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:qvec AS vector) ASC
            LIMIT :limit
            """
        ),
        {"qvec": _vector_literal(query_vector), "line_id": line_id, "limit": k},
    )).mappings().all()

    return [
        RetrievedMemory(
            memory_id=r["id"],
            category=r["category"],
            content=r["content"],
            confidence=r["confidence"],
            similarity=float(r["similarity"]),
        )
        for r in rows
    ]


async def mark_memories_accessed(
    session: AsyncSession, memory_ids: list[UUID]
) -> None:
    if not memory_ids:
        return
    await session.execute(
        text(
            """
            UPDATE line_memory
            SET access_count = access_count + 1, last_accessed = NOW()
            WHERE id = ANY(:ids)
            """
        ),
        {"ids": memory_ids},
    )
