"""Append-only audit log writer with SHA-256 hash chain (TDD §14).

Every row stores `audit_hash = SHA256(prev_audit_hash || canonical_json(payload))`
where `prev_audit_hash` is the most recent row's `audit_hash` (or '' for the
first row), and `canonical_json` uses sorted keys + minimal separators so
that two semantically-equal payloads always produce the same digest.

Verification: `verify_audit_chain()` re-computes every row's hash in
created_at order and asserts it matches what's stored. Any rebreak (gap,
missing prev_hash, mismatched payload) raises.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Canonical JSON + hashing
# ---------------------------------------------------------------------------


def _json_default(o: Any) -> Any:
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, UUID):
        return str(o)
    return str(o)


def canonical_json(payload: Any) -> str:
    """Deterministic JSON encoding: sorted keys, minimal separators."""
    return json.dumps(
        payload,
        default=_json_default,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def compute_audit_hash(prev_hash: str | None, payload: Any) -> str:
    """SHA-256(prev_hash || canonical_json(payload)) per TDD §14."""
    body = (prev_hash or "") + canonical_json(payload)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# Backward-compat alias used by other modules importing from audit.
def _json_safe(obj: Any) -> str:
    return canonical_json(obj)


# ---------------------------------------------------------------------------
# Writer (chain-extending)
# ---------------------------------------------------------------------------


async def _last_audit_hash(session: "AsyncSession") -> str | None:
    from sqlalchemy import text
    row = (await session.execute(
        text(
            """
            SELECT audit_hash FROM audit_log
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        )
    )).first()
    return row[0] if row else None


async def write_audit(
    session: "AsyncSession",
    *,
    event_type: str,
    user_id: str | None = None,
    session_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> str:
    """Append one row to audit_log and return the new row's audit_hash."""
    from sqlalchemy import text
    payload = {
        "event_type": event_type,
        "user_id": user_id,
        "session_id": session_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "details": details or {},
    }
    prev = await _last_audit_hash(session)
    new_hash = compute_audit_hash(prev, payload)
    await session.execute(
        text(
            """
            INSERT INTO audit_log
                (event_type, user_id, session_id, entity_type, entity_id,
                 details, audit_hash)
            VALUES
                (:event_type, :user_id, :session_id, :entity_type, :entity_id,
                 CAST(:details AS jsonb), :audit_hash)
            """
        ),
        {
            "event_type": event_type,
            "user_id": user_id,
            "session_id": session_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "details": canonical_json(details or {}),
            "audit_hash": new_hash,
        },
    )
    return new_hash


# ---------------------------------------------------------------------------
# Chain verifier (used by tests + admin tools)
# ---------------------------------------------------------------------------


async def verify_audit_chain(session: "AsyncSession", *, limit: int | None = None) -> int:
    """Recompute every row's hash; raise on first mismatch.

    Returns the number of rows verified. O(N); intended for nightly job
    or operator on-demand audit, not the chat hot path.
    """
    from sqlalchemy import text
    sql = """
        SELECT id, event_type, user_id, session_id, entity_type, entity_id,
               details, audit_hash, created_at
        FROM audit_log
        ORDER BY created_at ASC, id ASC
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = (await session.execute(text(sql))).mappings().all()

    prev: str | None = None
    verified = 0
    for r in rows:
        payload = {
            "event_type": r["event_type"],
            "user_id": r["user_id"],
            "session_id": r["session_id"],
            "entity_type": r["entity_type"],
            "entity_id": r["entity_id"],
            "details": r["details"] or {},
        }
        expected = compute_audit_hash(prev, payload)
        if r["audit_hash"] != expected:
            raise RuntimeError(
                f"audit chain break at row id={r['id']} "
                f"(created_at={r['created_at']}): "
                f"expected={expected}, stored={r['audit_hash']}"
            )
        prev = r["audit_hash"]
        verified += 1
    return verified
