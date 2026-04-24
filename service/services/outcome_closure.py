"""
Sprint 6 / B10 — Outcome closure scaffolding.

Two responsibilities:

  1. `find_pending_followups()` — find chat messages whose
     anchor_type == "past_event" that are older than
     `outcome_followup_hours` and have NO row in `outcome_linkages`. The
     calling job logs / surfaces these to operators so they can close
     the loop manually (or trigger an email reminder).

  2. `compute_precision_per_failure_mode()` — aggregate the assistant
     messages whose root-cause hypothesis was later confirmed/rejected
     by an outcome linkage and emit the per-failure-mode precision
     numbers. This drives the `v_rca_precision_daily` analytic view.

Both are pure read functions; the write (a small materialized view) is
left to a SQL migration to keep this layer stateless.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings


@dataclass
class PendingFollowup:
    message_id: str
    conversation_id: str
    user_id: str
    line_id: str | None
    failure_mode: str | None
    age_hours: float


@dataclass
class FailureModePrecision:
    failure_mode: str
    n_messages: int
    n_confirmed: int
    n_rejected: int
    precision: float | None  # confirmed / (confirmed + rejected)


async def find_pending_followups(
    session: AsyncSession,
    *,
    line_id: str | None = None,
    limit: int = 200,
) -> list[PendingFollowup]:
    """Past-event messages older than `outcome_followup_hours` with no linkage."""
    s = get_settings()
    hours = s.outcome_followup_hours

    sql = text(
        """
        SELECT
            m.id            AS message_id,
            m.conversation_id,
            c.user_id,
            c.line_id,
            (m.context_snapshot #>> '{parsed_anchor,failure_mode_scope}') AS fm,
            EXTRACT(EPOCH FROM (NOW() - m.created_at)) / 3600.0 AS age_hours
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE m.role = 'assistant'
          AND m.created_at < NOW() - (:h || ' hours')::interval
          AND (m.context_snapshot #>> '{parsed_anchor,anchor_type}') = 'past_event'
          AND (:line IS NULL OR c.line_id = :line)
          AND NOT EXISTS (
              SELECT 1 FROM outcome_linkages ol WHERE ol.message_id = m.id
          )
        ORDER BY m.created_at DESC
        LIMIT :lim
        """
    )
    rows = (await session.execute(
        sql, {"h": hours, "line": line_id, "lim": limit},
    )).mappings().all()
    return [
        PendingFollowup(
            message_id=str(r["message_id"]),
            conversation_id=str(r["conversation_id"]),
            user_id=r["user_id"],
            line_id=r["line_id"],
            failure_mode=r["fm"],
            age_hours=float(r["age_hours"] or 0.0),
        )
        for r in rows
    ]


async def compute_precision_per_failure_mode(
    session: AsyncSession,
    *,
    line_id: str | None = None,
    days: int = 30,
) -> list[FailureModePrecision]:
    """Aggregate confirmed vs. rejected outcome linkages by failure mode."""
    sql = text(
        """
        SELECT
            COALESCE(m.context_snapshot #>> '{parsed_anchor,failure_mode_scope}',
                     '(unspecified)') AS failure_mode,
            COUNT(*)                                                AS n_messages,
            SUM(CASE WHEN ol.alignment = 'confirmed' THEN 1 ELSE 0 END) AS n_confirmed,
            SUM(CASE WHEN ol.alignment = 'rejected'  THEN 1 ELSE 0 END) AS n_rejected
        FROM outcome_linkages ol
        JOIN messages       m ON m.id = ol.message_id
        JOIN conversations  c ON c.id = m.conversation_id
        WHERE ol.created_at >= NOW() - (:d || ' days')::interval
          AND (:line IS NULL OR c.line_id = :line)
        GROUP BY 1
        ORDER BY n_messages DESC
        """
    )
    rows = (await session.execute(sql, {"d": days, "line": line_id})).mappings().all()
    out: list[FailureModePrecision] = []
    for r in rows:
        confirmed = int(r["n_confirmed"] or 0)
        rejected = int(r["n_rejected"] or 0)
        denom = confirmed + rejected
        precision = confirmed / denom if denom > 0 else None
        out.append(FailureModePrecision(
            failure_mode=r["failure_mode"] or "(unspecified)",
            n_messages=int(r["n_messages"] or 0),
            n_confirmed=confirmed,
            n_rejected=rejected,
            precision=precision,
        ))
    return out
