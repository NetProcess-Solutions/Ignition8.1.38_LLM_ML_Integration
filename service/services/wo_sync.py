"""
Work order synchronization (design section 7.2).

Nightly job that pulls work orders from the Ignition WO database and:

  1. Upserts them into the local `work_orders` table (structured fields).
  2. Re-ingests the WO narrative (problem_description + resolution_notes)
     into `documents` + `document_chunks` so semantic retrieval can find
     them by symptom phrasing. The doc inherits source_type='work_order'
     and document_role='work_order' so the role-weighting in retrieval
     blends WOs into the same evidence stream as maintenance reports.

This module is intentionally pull-only: the Ignition WO DB stays the
source of truth. Re-running is safe (upsert + chunk replace).

Ignition's WO DB is typically reachable as another SQLAlchemy URL exposed
via settings.ignition_wo_db_url. If unset, sync() raises a configuration
error with a clear message instead of silently no-oping.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from config.settings import get_settings


@dataclass
class WorkOrderRow:
    wo_number: str
    line_id: str
    equipment_id: str | None
    wo_type: str | None
    problem_description: str | None
    resolution_notes: str | None
    date_opened: datetime | None
    date_closed: datetime | None
    technician: str | None
    metadata: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Source-side adapter
# ---------------------------------------------------------------------------

async def _fetch_recent_from_ignition(since: datetime) -> list[WorkOrderRow]:
    """
    Pulls work orders modified at or after `since` from the Ignition WO DB.
    The exact column names are plant-specific; this query targets the
    Shaw Plant 4 schema. Adjust if column names change.
    """
    url = getattr(get_settings(), "ignition_wo_db_url", None)
    if not url:
        raise RuntimeError(
            "ignition_wo_db_url is not configured; cannot run wo_sync. "
            "Set IGNITION_WO_DB_URL in the service environment."
        )
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT
                        wo_number, line_id, equipment_id, wo_type,
                        problem_description, resolution_notes,
                        date_opened, date_closed, technician
                    FROM work_orders
                    WHERE COALESCE(date_modified, date_opened) >= :since
                    """
                ),
                {"since": since},
            )
            rows = result.mappings().all()
    finally:
        await engine.dispose()
    return [
        WorkOrderRow(
            wo_number=r["wo_number"],
            line_id=r["line_id"],
            equipment_id=r["equipment_id"],
            wo_type=r["wo_type"],
            problem_description=r["problem_description"],
            resolution_notes=r["resolution_notes"],
            date_opened=r["date_opened"],
            date_closed=r["date_closed"],
            technician=r["technician"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Target-side writes
# ---------------------------------------------------------------------------

async def _upsert_work_order(session: AsyncSession, wo: WorkOrderRow) -> uuid.UUID:
    existing = (await session.execute(
        text("SELECT id FROM work_orders WHERE wo_number = :n"),
        {"n": wo.wo_number},
    )).first()
    if existing:
        wo_id = existing[0]
        await session.execute(
            text(
                """
                UPDATE work_orders SET
                    line_id = :line, equipment_id = :eq, wo_type = :tp,
                    problem_description = :pd, resolution_notes = :rn,
                    date_opened = :do, date_closed = :dc,
                    technician = :tech, metadata = CAST(:md AS jsonb),
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "line": wo.line_id, "eq": wo.equipment_id, "tp": wo.wo_type,
                "pd": wo.problem_description, "rn": wo.resolution_notes,
                "do": wo.date_opened, "dc": wo.date_closed,
                "tech": wo.technician, "md": json.dumps(wo.metadata or {}),
                "id": wo_id,
            },
        )
        return wo_id
    wo_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO work_orders (
                id, wo_number, line_id, equipment_id, wo_type,
                problem_description, resolution_notes,
                date_opened, date_closed, technician, metadata
            ) VALUES (
                :id, :n, :line, :eq, :tp, :pd, :rn,
                :do, :dc, :tech, CAST(:md AS jsonb)
            )
            """
        ),
        {
            "id": wo_id, "n": wo.wo_number, "line": wo.line_id,
            "eq": wo.equipment_id, "tp": wo.wo_type,
            "pd": wo.problem_description, "rn": wo.resolution_notes,
            "do": wo.date_opened, "dc": wo.date_closed,
            "tech": wo.technician, "md": json.dumps(wo.metadata or {}),
        },
    )
    return wo_id


def _wo_narrative(wo: WorkOrderRow) -> str:
    parts = [
        f"Work Order {wo.wo_number}",
        f"Equipment: {wo.equipment_id or 'unknown'}",
        f"Type: {wo.wo_type or 'unknown'}",
        f"Opened: {wo.date_opened.isoformat() if wo.date_opened else 'unknown'}",
        f"Closed: {wo.date_closed.isoformat() if wo.date_closed else 'still open'}",
        f"Technician: {wo.technician or 'unknown'}",
        "",
        "Problem:",
        (wo.problem_description or "(none provided)").strip(),
        "",
        "Resolution:",
        (wo.resolution_notes or "(none provided)").strip(),
    ]
    return "\n".join(parts)


async def _dual_ingest_narrative(
    session: AsyncSession, wo: WorkOrderRow, wo_id: uuid.UUID,
) -> int:
    """
    Re-ingest the WO narrative into documents + document_chunks so the RAG
    layer can find this WO by symptom phrasing. document_role='work_order'.
    """
    body = _wo_narrative(wo)
    title = f"Work Order {wo.wo_number}"

    existing = (await session.execute(
        text(
            """
            SELECT id FROM documents
            WHERE source_type = 'work_order' AND source_id = :sid
            """
        ),
        {"sid": wo.wo_number},
    )).first()

    if existing:
        doc_id = existing[0]
        await session.execute(
            text("DELETE FROM document_chunks WHERE document_id = :id"),
            {"id": doc_id},
        )
        await session.execute(
            text(
                """
                UPDATE documents SET
                    title = :title, raw_text = :rt,
                    document_date = :dd,
                    document_role = 'work_order',
                    document_weight = 1.15,
                    metadata = CAST(:md AS jsonb),
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "title": title, "rt": body,
                "dd": wo.date_closed or wo.date_opened,
                "md": json.dumps({
                    "wo_id": str(wo_id),
                    "wo_number": wo.wo_number,
                    "equipment_id": wo.equipment_id,
                    "document_role": "work_order",
                    "document_weight": 1.15,
                }),
                "id": doc_id,
            },
        )
    else:
        doc_id = uuid.uuid4()
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, source_type, source_id, line_id, title,
                    document_date, raw_text,
                    document_role, document_weight, metadata
                ) VALUES (
                    :id, 'work_order', :sid, :line, :title,
                    :dd, :rt,
                    'work_order', 1.15, CAST(:md AS jsonb)
                )
                """
            ),
            {
                "id": doc_id, "sid": wo.wo_number, "line": wo.line_id,
                "title": title,
                "dd": wo.date_closed or wo.date_opened, "rt": body,
                "md": json.dumps({
                    "wo_id": str(wo_id),
                    "wo_number": wo.wo_number,
                    "equipment_id": wo.equipment_id,
                    "document_role": "work_order",
                    "document_weight": 1.15,
                }),
            },
        )

    # Lazy imports keep this module importable even when the embedding
    # model isn't available (e.g. in unit tests).
    from services.chunker import APPROX_CHARS_PER_TOKEN, chunk_text
    from services.embeddings import embed_sync

    chunks = chunk_text(body)
    if not chunks:
        return 0
    embeddings = embed_sync(chunks)
    for i, (ctext, vec) in enumerate(zip(chunks, embeddings)):
        vec_literal = "[" + ",".join(f"{v:.7f}" for v in vec) + "]"
        await session.execute(
            text(
                """
                INSERT INTO document_chunks (
                    document_id, chunk_index, chunk_text, embedding, token_count
                ) VALUES (:doc, :idx, :txt, CAST(:vec AS vector), :tc)
                """
            ),
            {
                "doc": doc_id, "idx": i, "txt": ctext, "vec": vec_literal,
                "tc": max(1, len(ctext) // APPROX_CHARS_PER_TOKEN),
            },
        )
    return len(chunks)


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

async def sync_since(session: AsyncSession, since: datetime) -> dict[str, int]:
    """Pull WOs modified since `since`, upsert + dual-ingest. Returns counts."""
    rows = await _fetch_recent_from_ignition(since)
    upserted = 0
    chunks_total = 0
    for wo in rows:
        wo_id = await _upsert_work_order(session, wo)
        chunks_total += await _dual_ingest_narrative(session, wo, wo_id)
        upserted += 1
    await session.commit()
    return {"upserted": upserted, "chunks": chunks_total}
