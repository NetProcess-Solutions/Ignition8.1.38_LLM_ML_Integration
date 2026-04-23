"""
Document ingestion pipeline.

Reads raw report files, parses by source type, chunks, embeds, and stores
in PostgreSQL. Designed to be re-run safely (idempotent on (source_type,
source_id) for documents).

Formats supported in MVP:
  - .txt / .md
  - .csv (treated as one document per row OR as a single tabular document,
    configurable per source type)
  - .json (structured event data, routed to event tables)

PDF/Word/Excel parsing: add adapters in `parsers/` as your real report
formats become known. The interface is intentionally minimal so adding
a new adapter is small.

Usage:
    docker compose exec ai-service python -m scripts.ingest \
        --source-type maintenance_report \
        --line-id coater1 \
        --path /app/data/incoming/maintenance/

    docker compose exec ai-service python -m scripts.ingest \
        --source-type quality_report_csv \
        --line-id coater1 \
        --path /app/data/incoming/quality.csv
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import SessionFactory  # noqa: E402
from services.chunker import APPROX_CHARS_PER_TOKEN, chunk_text  # noqa: E402,F401
from services.embeddings import embed_sync  # noqa: E402


@dataclass
class ParsedDocument:
    source_type: str
    source_id: str
    line_id: str
    title: str
    raw_text: str
    document_date: datetime | None = None
    author: str | None = None
    shift: str | None = None
    structured_fields: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


# -----------------------------------------------------------------------------
# Adapters
# -----------------------------------------------------------------------------

def parse_text_files(directory: Path, source_type: str, line_id: str) -> Iterable[ParsedDocument]:
    """One ParsedDocument per .txt/.md file."""
    for fp in sorted(directory.glob("**/*")):
        if not fp.is_file():
            continue
        if fp.suffix.lower() not in (".txt", ".md"):
            continue
        text_body = fp.read_text(encoding="utf-8", errors="replace")
        yield ParsedDocument(
            source_type=source_type,
            source_id=fp.name,
            line_id=line_id,
            title=fp.stem,
            raw_text=text_body,
            document_date=datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc),
            metadata={"source_path": str(fp)},
        )


def parse_quality_csv(path: Path, line_id: str) -> tuple[list[ParsedDocument], list[dict[str, Any]]]:
    """
    Parse a quality results CSV. Expected columns (flexible naming):
        test_time | test_type | result | sample_id | notes | run_number

    Returns a list of ParsedDocument (for RAG corpus) AND a list of structured
    rows (for the quality_results table). Both are useful: text for retrieval,
    structured for event-window queries and ML training.
    """
    import csv

    docs: list[ParsedDocument] = []
    structured: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader):
            test_time_raw = row.get("test_time") or row.get("date") or row.get("timestamp")
            try:
                test_time = datetime.fromisoformat(test_time_raw) if test_time_raw else None
            except (ValueError, TypeError):
                test_time = None
            test_type = (row.get("test_type") or row.get("type") or "unknown").strip()
            result = (row.get("result") or row.get("outcome") or "unknown").strip().lower()
            if result not in ("pass", "fail", "marginal", "retest"):
                result = "marginal"
            sample_id = row.get("sample_id") or row.get("sample") or f"row-{i}"
            notes = row.get("notes") or ""

            structured.append({
                "test_time": test_time,
                "test_type": test_type,
                "result": result,
                "sample_id": sample_id,
                "notes": notes,
                "raw": row,
            })

            text_body = (
                f"Quality test: {test_type}\n"
                f"Time: {test_time}\n"
                f"Sample: {sample_id}\n"
                f"Result: {result.upper()}\n"
                f"Notes: {notes}\n"
            )
            docs.append(ParsedDocument(
                source_type="quality_report",
                source_id=f"{path.name}#{i}",
                line_id=line_id,
                title=f"Quality {test_type} {sample_id}",
                raw_text=text_body,
                document_date=test_time,
                structured_fields={
                    "test_type": test_type, "result": result, "sample_id": sample_id,
                },
                metadata={"source_csv": path.name, "row": i},
            ))
    return docs, structured


# -----------------------------------------------------------------------------
# Storage
# -----------------------------------------------------------------------------

# Per design §5.6: document_role drives document_weight, which multiplies the
# similarity * (1 + quality_adj) score at retrieval time. Plant-specific
# evidence (memory, work_orders, internal SOPs) outranks textbook/vendor docs.
DOCUMENT_ROLE_BY_SOURCE_TYPE: dict[str, str] = {
    "maintenance_report": "maintenance_history",
    "work_order": "work_order",
    "downtime_log": "operational_log",
    "quality_report": "quality_record",
    "quality_report_csv": "quality_record",
    "shift_handoff": "operational_log",
    "sop": "internal_sop",
    "internal_sop": "internal_sop",
    "vendor_manual": "vendor_doc",
    "tribal_knowledge": "tribal_knowledge",
    "training_material": "training_material",
    "engineering_note": "engineering_note",
}

DOCUMENT_WEIGHT_BY_ROLE: dict[str, float] = {
    "tribal_knowledge": 1.30,
    "internal_sop": 1.20,
    "engineering_note": 1.20,
    "maintenance_history": 1.15,
    "work_order": 1.15,
    "operational_log": 1.10,
    "quality_record": 1.10,
    "training_material": 0.85,
    "vendor_doc": 0.70,
}


def role_and_weight_for(source_type: str) -> tuple[str, float]:
    role = DOCUMENT_ROLE_BY_SOURCE_TYPE.get(source_type, "operational_log")
    weight = DOCUMENT_WEIGHT_BY_ROLE.get(role, 1.0)
    return role, weight


async def upsert_document(session, doc: ParsedDocument, batch_id: uuid.UUID) -> uuid.UUID:
    role, weight = role_and_weight_for(doc.source_type)
    # Stamp role/weight into metadata so downstream consumers always see it
    # even if the column read path changes.
    md = dict(doc.metadata or {})
    md.setdefault("document_role", role)
    md.setdefault("document_weight", weight)

    existing = (await session.execute(
        text(
            """
            SELECT id FROM documents
            WHERE source_type = :st AND source_id = :sid AND line_id = :line
            """
        ),
        {"st": doc.source_type, "sid": doc.source_id, "line": doc.line_id},
    )).first()

    if existing:
        doc_id = existing[0]
        # Replace chunks (simpler than diffing for MVP).
        await session.execute(
            text("DELETE FROM document_chunks WHERE document_id = :id"),
            {"id": doc_id},
        )
        await session.execute(
            text(
                """
                UPDATE documents SET
                    title = :title, author = :author, document_date = :ddate,
                    shift = :shift, raw_text = :rt,
                    document_role = :role, document_weight = :weight,
                    structured_fields = CAST(:sf AS jsonb),
                    metadata = CAST(:md AS jsonb),
                    ingestion_batch_id = :batch, updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "title": doc.title, "author": doc.author, "ddate": doc.document_date,
                "shift": doc.shift, "rt": doc.raw_text,
                "role": role, "weight": weight,
                "sf": json.dumps(doc.structured_fields or {}),
                "md": json.dumps(md),
                "batch": batch_id, "id": doc_id,
            },
        )
        return doc_id

    doc_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO documents (
                id, source_type, source_id, line_id, title, author,
                document_date, shift, raw_text,
                document_role, document_weight,
                structured_fields, metadata,
                ingestion_batch_id
            ) VALUES (
                :id, :st, :sid, :line, :title, :author,
                :ddate, :shift, :rt,
                :role, :weight,
                CAST(:sf AS jsonb), CAST(:md AS jsonb),
                :batch
            )
            """
        ),
        {
            "id": doc_id, "st": doc.source_type, "sid": doc.source_id,
            "line": doc.line_id, "title": doc.title, "author": doc.author,
            "ddate": doc.document_date, "shift": doc.shift, "rt": doc.raw_text,
            "role": role, "weight": weight,
            "sf": json.dumps(doc.structured_fields or {}),
            "md": json.dumps(md),
            "batch": batch_id,
        },
    )
    return doc_id


async def insert_chunks(session, doc_id: uuid.UUID, chunks: list[str], embeddings: list[list[float]]) -> int:
    for i, (ctext, vec) in enumerate(zip(chunks, embeddings)):
        vec_literal = "[" + ",".join(f"{v:.7f}" for v in vec) + "]"
        await session.execute(
            text(
                """
                INSERT INTO document_chunks (document_id, chunk_index, chunk_text, embedding, token_count)
                VALUES (:doc, :idx, :txt, CAST(:vec AS vector), :tc)
                """
            ),
            {
                "doc": doc_id, "idx": i, "txt": ctext, "vec": vec_literal,
                "tc": max(1, len(ctext) // APPROX_CHARS_PER_TOKEN),
            },
        )
    return len(chunks)


async def insert_quality_rows(session, line_id: str, rows: list[dict[str, Any]]) -> int:
    n = 0
    for r in rows:
        if not r["test_time"]:
            continue
        await session.execute(
            text(
                """
                INSERT INTO quality_results (
                    line_id, test_type, test_time, sample_id, result,
                    measurements, notes, metadata
                ) VALUES (
                    :line, :tt, :ttime, :sid, :res,
                    CAST(:m AS jsonb), :notes, CAST(:md AS jsonb)
                )
                """
            ),
            {
                "line": line_id, "tt": r["test_type"], "ttime": r["test_time"],
                "sid": r["sample_id"], "res": r["result"],
                "m": json.dumps({}), "notes": r["notes"],
                "md": json.dumps({"raw": r["raw"]}),
            },
        )
        n += 1
    return n


# -----------------------------------------------------------------------------
# Driver
# -----------------------------------------------------------------------------

async def run(source_type: str, line_id: str, path_str: str) -> None:
    path = Path(path_str)
    if not path.exists():
        raise SystemExit(f"path does not exist: {path}")

    batch_id = uuid.uuid4()
    docs: list[ParsedDocument] = []
    structured_quality: list[dict[str, Any]] = []

    if source_type == "quality_report_csv" and path.is_file() and path.suffix.lower() == ".csv":
        d, s = parse_quality_csv(path, line_id)
        docs.extend(d)
        structured_quality.extend(s)
    elif path.is_dir():
        docs.extend(list(parse_text_files(path, source_type, line_id)))
    elif path.suffix.lower() in (".txt", ".md"):
        text_body = path.read_text(encoding="utf-8", errors="replace")
        docs.append(ParsedDocument(
            source_type=source_type, source_id=path.name, line_id=line_id,
            title=path.stem, raw_text=text_body,
            document_date=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
        ))
    else:
        raise SystemExit(
            f"don't know how to ingest source_type={source_type} from {path}"
        )

    # Embed all chunks in batch for efficiency
    print(f"Parsed {len(docs)} document(s); chunking and embedding...")
    all_chunks: list[tuple[int, list[str]]] = []
    flat_chunks: list[str] = []
    for i, doc in enumerate(docs):
        chunks = chunk_text(doc.raw_text)
        all_chunks.append((i, chunks))
        flat_chunks.extend(chunks)
    embeddings = embed_sync(flat_chunks) if flat_chunks else []

    async with SessionFactory() as session:
        await session.execute(
            text(
                """
                INSERT INTO ingestion_runs (id, source_type, started_at, triggered_by, notes)
                VALUES (:id, :st, NOW(), 'cli', :notes)
                """
            ),
            {"id": batch_id, "st": source_type, "notes": f"path={path}"},
        )
        offset = 0
        total_chunks = 0
        for (i, chunks), doc in zip(all_chunks, docs):
            doc_id = await upsert_document(session, doc, batch_id)
            doc_embeds = embeddings[offset:offset + len(chunks)]
            offset += len(chunks)
            n = await insert_chunks(session, doc_id, chunks, doc_embeds)
            total_chunks += n

        n_quality = 0
        if structured_quality:
            n_quality = await insert_quality_rows(session, line_id, structured_quality)

        await session.execute(
            text(
                """
                UPDATE ingestion_runs SET
                    completed_at = NOW(),
                    documents_processed = :dp,
                    chunks_created = :cc,
                    notes = COALESCE(notes, '') || :extra
                WHERE id = :id
                """
            ),
            {"dp": len(docs), "cc": total_chunks,
             "extra": f" | quality_rows={n_quality}", "id": batch_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO audit_log (event_type, entity_type, entity_id, details)
                VALUES ('ingestion_completed', 'ingestion_run', :id, CAST(:d AS jsonb))
                """
            ),
            {"id": str(batch_id),
             "d": json.dumps({
                 "source_type": source_type, "line_id": line_id,
                 "path": str(path), "documents": len(docs),
                 "chunks": total_chunks, "quality_rows": n_quality,
             })},
        )
        await session.commit()
    print(f"Done. documents={len(docs)} chunks={total_chunks} quality_rows={n_quality}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source-type", required=True,
                   help="e.g. maintenance_report, downtime_report, quality_report_csv, sop, note")
    p.add_argument("--line-id", default="coater1")
    p.add_argument("--path", required=True)
    args = p.parse_args()
    asyncio.run(run(args.source_type, args.line_id, args.path))


if __name__ == "__main__":
    main()
