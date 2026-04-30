"""
Conditional baseline cache (design Task 11).

Maintains precomputed per-tag aggregates keyed by:
  - (style, front_step, last_4_runs)        → "last 4 runs" bucket
  - (style, failure_mode, prior_event_set)  → "failure-mode-matched" bucket

In MVP this is a cache-on-read implementation backed by `production_runs`,
`defect_events`, and a `historian.read_window()` accessor that the gateway
populates when an anchor query hits cold cache. Background invalidation
fires on (a) production_runs.status → 'completed', and
(b) new defect_events row inserted for an existing run.

The cache itself lives in `feature_snapshots` so it benefits from the same
backup/replication path as everything else; we use the
`feature_set_version='baseline_cache_v1'` namespace.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Sequence
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


CACHE_FEATURE_SET = "baseline_cache_v1"


@dataclass
class TagBaseline:
    """Per-tag aggregate over a baseline window."""
    tag_name: str
    samples: list[float] = field(default_factory=list)
    mean: float | None = None
    min: float | None = None
    max: float | None = None
    std: float | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None

    def fill_stats(self) -> None:
        if not self.samples:
            return
        self.mean = statistics.mean(self.samples)
        self.min = min(self.samples)
        self.max = max(self.samples)
        if len(self.samples) > 1:
            self.std = statistics.pstdev(self.samples)
        else:
            self.std = 0.0


# ---------------------------------------------------------------------------
# Last-4-runs cache
# ---------------------------------------------------------------------------

async def get_last_n_runs(
    session: AsyncSession,
    *,
    line_id: str,
    style: str,
    front_step: int | None,
    before: datetime,
    n: int = 4,
) -> list[dict[str, Any]]:
    """The N most recent completed runs of (style, front_step) before `before`."""
    sql = text(
        """
        SELECT id, run_number, start_time, end_time, product_style, front_step
        FROM production_runs
        WHERE line_id = :line
          AND product_style = :style
          AND (:fs IS NULL OR front_step = :fs)
          AND status = 'completed'
          AND end_time IS NOT NULL
          AND end_time < :before
        ORDER BY end_time DESC
        LIMIT :n
        """
    )
    rows = (await session.execute(
        sql,
        {"line": line_id, "style": style, "fs": front_step, "before": before, "n": n},
    )).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Failure-mode-matched history (the dominant grounding bucket)
# ---------------------------------------------------------------------------

async def get_failure_mode_matched_runs(
    session: AsyncSession,
    *,
    line_id: str,
    style: str,
    failure_mode: str,
    before: datetime | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """
    Every prior production_run matching (style, failure_mode) before
    `before`. Joins through defect_events (and quality_results when
    failure_mode is encoded there too).
    """
    sql = text(
        """
        SELECT
            r.id          AS run_id,
            r.run_number,
            r.product_style,
            r.front_step,
            r.recipe_id,
            r.target_specs,
            r.metadata,
            r.start_time,
            r.end_time,
            d.id          AS defect_id,
            d.fm_code,
            d.detected_time
        FROM defect_events d
        JOIN production_runs r ON r.id = d.run_id
        WHERE r.line_id = :line
          AND r.product_style = :style
          AND d.fm_code = :fm
          AND (:before IS NULL OR d.detected_time < :before)
        ORDER BY d.detected_time DESC
        LIMIT :lim
        """
    )
    rows = (await session.execute(
        sql,
        {"line": line_id, "style": style, "fm": failure_mode,
         "before": before, "lim": limit},
    )).mappings().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        # Surface metadata.crew (if present) to a top-level "crew" key so
        # downstream code (services.change_ledger) can read it without
        # knowing the metadata layout.
        meta = d.get("metadata") or {}
        if isinstance(meta, dict):
            if "crew" in meta and "crew" not in d:
                d["crew"] = meta.get("crew")
            if "shift" in meta and "shift" not in d:
                d["shift"] = meta.get("shift")
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Cache read / write
# ---------------------------------------------------------------------------

async def cache_read(
    session: AsyncSession,
    *,
    cache_key: str,
) -> dict[str, Any] | None:
    sql = text(
        """
        SELECT features FROM feature_snapshots
        WHERE feature_set_version = :fsv
          AND label = :key
        ORDER BY created_at DESC
        LIMIT 1
        """
    )
    row = (await session.execute(
        sql, {"fsv": CACHE_FEATURE_SET, "key": cache_key},
    )).mappings().first()
    if not row:
        return None
    return dict(row["features"] or {})


async def cache_write(
    session: AsyncSession,
    *,
    cache_key: str,
    payload: dict[str, Any],
    run_id: UUID | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> None:
    sql = text(
        """
        INSERT INTO feature_snapshots
            (id, run_id, feature_set_version, features, label, label_source,
             window_start, window_end)
        VALUES
            (:id, :run, :fsv, CAST(:features AS jsonb), :key, 'baseline_cache',
             :ws, :we)
        """
    )
    await session.execute(
        sql,
        {
            "id": uuid4(),
            "run": run_id,
            "fsv": CACHE_FEATURE_SET,
            "features": json.dumps(payload, default=str),
            "key": cache_key,
            "ws": window_start,
            "we": window_end,
        },
    )


async def cache_invalidate(
    session: AsyncSession,
    *,
    cache_key_prefix: str,
) -> int:
    """Best-effort invalidation by prefix; returns rows deleted."""
    sql = text(
        """
        DELETE FROM feature_snapshots
        WHERE feature_set_version = :fsv
          AND label LIKE :prefix
        """
    )
    res = await session.execute(
        sql, {"fsv": CACHE_FEATURE_SET, "prefix": cache_key_prefix + "%"}
    )
    return res.rowcount or 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_last_n_cache_key(*, line_id: str, style: str,
                           front_step: int | None, n: int = 4) -> str:
    return f"last{n}|{line_id}|{style}|fs={front_step or 'any'}"


def build_failure_mode_cache_key(*, line_id: str, style: str,
                                 failure_mode: str) -> str:
    return f"fmm|{line_id}|{style}|{failure_mode}"


def normal_baseline_window(*, anchor_time: datetime, days_prior: int = 14,
                           window_hours: int = 24) -> tuple[datetime, datetime]:
    """The 14-day-prior normal-operation reference window per §3.3."""
    end = anchor_time - timedelta(days=days_prior)
    start = end - timedelta(hours=window_hours)
    return start, end


def aggregate_samples(samples: Sequence[float | int | None]) -> dict[str, Any]:
    cleaned = [float(v) for v in samples if v is not None]
    if not cleaned:
        return {"samples": [], "mean": None, "min": None, "max": None, "std": None}
    return {
        "samples": cleaned,
        "mean": statistics.mean(cleaned),
        "min": min(cleaned),
        "max": max(cleaned),
        "std": statistics.pstdev(cleaned) if len(cleaned) > 1 else 0.0,
    }
