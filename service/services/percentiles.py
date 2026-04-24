"""
Sprint 3 / B0.1–B0.3 — Distributional grounding service.

Pure-unsupervised statistics on top of `feature_snapshots` joined with
`production_runs`. Returns:

  * `percentile_of(tag, value, scope)`         — where this value sits in
                                                 the empirical CDF.
  * `compare_to_distribution(tag, value, ...)` — percentile + nearest
                                                 historical runs (similar
                                                 values), labeled by
                                                 outcome where known.
  * `nearest_historical_runs(tag, value, ...)` — top-K runs whose feature
                                                 value is closest to
                                                 `value`, with their
                                                 product_style + outcome.
  * `detect_drift(tag, scope)`                 — Page-Hinkley CUSUM on a
                                                 90-day rolling daily mean.

The service does NOT mutate state. CDFs are cached in-process (TTL,
keyed by `(tag, scope_key)`) so the same query inside a single chat
turn doesn't re-issue SQL.

Why scope matters: a `Front2_Temp` of 198 °C is "high" globally, but
"normal" for style S-1234 at front_step=2 in summer. Tying the
percentile to context is the whole point.

Backed entirely by the existing schema (no new tables).
"""
from __future__ import annotations

import asyncio
import bisect
import math
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

ScopeKind = Literal[
    "global",
    "style",
    "style_step",
    "equipment",
    "recipe",
    "global_ytd",
]


@dataclass(frozen=True)
class Scope:
    """Distribution scope; immutable so it can key the cache."""

    kind: ScopeKind = "global"
    line_id: str | None = None
    product_style: str | None = None
    front_step: int | None = None
    equipment: str | None = None
    recipe_id: str | None = None
    # Inclusive lookback window in days; None = whole table.
    lookback_days: int | None = 365

    def cache_key(self) -> str:
        return "|".join(
            (
                self.kind,
                str(self.line_id),
                str(self.product_style),
                str(self.front_step),
                str(self.equipment),
                str(self.recipe_id),
                str(self.lookback_days),
            )
        )

    def describe(self) -> str:
        bits = [self.kind]
        if self.line_id:
            bits.append(f"line={self.line_id}")
        if self.product_style:
            bits.append(f"style={self.product_style}")
        if self.front_step is not None:
            bits.append(f"step={self.front_step}")
        if self.equipment:
            bits.append(f"equip={self.equipment}")
        if self.recipe_id:
            bits.append(f"recipe={self.recipe_id}")
        if self.lookback_days:
            bits.append(f"last={self.lookback_days}d")
        return ", ".join(bits)


@dataclass
class PercentileResult:
    tag: str
    value: float
    percentile: float | None  # 0..1, or None if no samples
    sample_size: int
    scope: Scope
    interpretation: Literal["very_low", "low", "typical", "high", "very_high", "unknown"]
    window_label: str  # human-readable window for citations

    def as_citation_payload(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "value": self.value,
            "percentile": self.percentile,
            "sample_size": self.sample_size,
            "scope": self.scope.describe(),
            "interpretation": self.interpretation,
            "window": self.window_label,
        }


@dataclass
class NearestRun:
    run_id: str
    run_number: str | None
    product_style: str | None
    front_step: int | None
    start_time_iso: str
    feature_value: float
    label: str | None  # snapshot label = often the failure mode or "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_number": self.run_number,
            "product_style": self.product_style,
            "front_step": self.front_step,
            "start_time": self.start_time_iso,
            "feature_value": self.feature_value,
            "label": self.label,
        }


@dataclass
class DistributionComparison:
    percentile: PercentileResult
    nearest_runs: list[NearestRun]
    nearest_outcomes: dict[str, int]  # label -> count among nearest


@dataclass
class DriftResult:
    tag: str
    scope: Scope
    drifted: bool
    statistic: float          # Page-Hinkley statistic
    threshold: float          # configured cutoff
    sample_size: int
    direction: Literal["up", "down", "none"]
    window_label: str


# ---------------------------------------------------------------------------
# In-process CDF cache
# ---------------------------------------------------------------------------

@dataclass
class _CachedSamples:
    sorted_values: list[float]
    rows: list[dict[str, Any]] = field(default_factory=list)  # full rows for nearest-run lookup
    fetched_at: float = field(default_factory=time.time)


_SAMPLE_CACHE: dict[tuple[str, str], _CachedSamples] = {}
_CACHE_TTL_S = 300  # 5 min — chat conversations are short
_CACHE_LOCK = asyncio.Lock()


def _interpret(p: float | None) -> str:
    if p is None:
        return "unknown"
    if p < 0.05:
        return "very_low"
    if p < 0.25:
        return "low"
    if p < 0.75:
        return "typical"
    if p < 0.95:
        return "high"
    return "very_high"


# ---------------------------------------------------------------------------
# Sample fetch — JSONB feature lookup with safe scoping
# ---------------------------------------------------------------------------

async def _fetch_samples(
    session: AsyncSession, tag: str, scope: Scope
) -> _CachedSamples:
    """
    Pull (tag-value, run-metadata) tuples from feature_snapshots ⨝ production_runs.

    The tag is looked up in the JSONB blob at three common shapes:
      1. features->>'<tag>'                       (flat scalar)
      2. features->'tag_aggregates'->'<tag>'->>'mean'   (per-tag sub-object)
      3. features->'<tag>'->>'mean'               (per-tag sub-object alt)
    First non-null wins.

    Scope filters are SQL-side; lookback_days uses production_runs.start_time
    so the window matches "production" wall-clock, not snapshot creation.
    """
    cache_key = (tag, scope.cache_key())
    async with _CACHE_LOCK:
        cached = _SAMPLE_CACHE.get(cache_key)
        if cached and (time.time() - cached.fetched_at) < _CACHE_TTL_S:
            return cached

    where_clauses: list[str] = []
    params: dict[str, Any] = {"tag": tag}

    if scope.line_id:
        where_clauses.append("pr.line_id = :line_id")
        params["line_id"] = scope.line_id
    if scope.product_style:
        where_clauses.append("pr.product_style = :product_style")
        params["product_style"] = scope.product_style
    if scope.front_step is not None:
        where_clauses.append("pr.front_step = :front_step")
        params["front_step"] = scope.front_step
    if scope.recipe_id:
        where_clauses.append("pr.recipe_id = :recipe_id")
        params["recipe_id"] = scope.recipe_id
    if scope.lookback_days:
        where_clauses.append("pr.start_time >= NOW() - (:lookback || ' days')::interval")
        params["lookback"] = str(int(scope.lookback_days))

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    sql = f"""
        SELECT
            pr.id::text                              AS run_id,
            pr.run_number,
            pr.product_style,
            pr.front_step,
            pr.start_time,
            fs.label,
            COALESCE(
                NULLIF(fs.features->>:tag, '')::float8,
                NULLIF(fs.features->'tag_aggregates'->:tag->>'mean', '')::float8,
                NULLIF(fs.features->:tag->>'mean', '')::float8
            ) AS v
        FROM feature_snapshots fs
        JOIN production_runs   pr ON pr.id = fs.run_id
        {where_sql}
        ORDER BY pr.start_time DESC
        LIMIT 50000
    """
    rows = (await session.execute(text(sql), params)).mappings().all()

    values: list[float] = []
    kept: list[dict[str, Any]] = []
    for r in rows:
        v = r["v"]
        if v is None or not math.isfinite(v):
            continue
        values.append(float(v))
        kept.append(dict(r))

    values.sort()
    cached = _CachedSamples(sorted_values=values, rows=kept)
    async with _CACHE_LOCK:
        _SAMPLE_CACHE[cache_key] = cached
    return cached


# ---------------------------------------------------------------------------
# Public API — percentile_of / compare_to_distribution / nearest_runs / drift
# ---------------------------------------------------------------------------

async def percentile_of(
    session: AsyncSession,
    tag: str,
    value: float,
    scope: Scope | None = None,
) -> PercentileResult:
    scope = scope or Scope()
    samples = await _fetch_samples(session, tag, scope)
    n = len(samples.sorted_values)

    if n == 0:
        return PercentileResult(
            tag=tag,
            value=value,
            percentile=None,
            sample_size=0,
            scope=scope,
            interpretation="unknown",
            window_label=scope.describe(),
        )

    rank = bisect.bisect_right(samples.sorted_values, value)
    pct = rank / n
    return PercentileResult(
        tag=tag,
        value=value,
        percentile=pct,
        sample_size=n,
        scope=scope,
        interpretation=_interpret(pct),  # type: ignore[arg-type]
        window_label=scope.describe(),
    )


async def nearest_historical_runs(
    session: AsyncSession,
    tag: str,
    value: float,
    scope: Scope | None = None,
    k: int = 5,
) -> list[NearestRun]:
    scope = scope or Scope()
    samples = await _fetch_samples(session, tag, scope)
    if not samples.rows:
        return []
    # Sort full rows by absolute distance; deterministic ties broken by recency.
    rows = sorted(
        samples.rows,
        key=lambda r: (abs(float(r["v"]) - value), -(r["start_time"].timestamp() if r["start_time"] else 0)),
    )[:k]
    out: list[NearestRun] = []
    for r in rows:
        out.append(
            NearestRun(
                run_id=r["run_id"],
                run_number=r.get("run_number"),
                product_style=r.get("product_style"),
                front_step=r.get("front_step"),
                start_time_iso=r["start_time"].isoformat() if r["start_time"] else "",
                feature_value=float(r["v"]),
                label=r.get("label"),
            )
        )
    return out


async def compare_to_distribution(
    session: AsyncSession,
    tag: str,
    value: float,
    scope: Scope | None = None,
    k: int = 5,
) -> DistributionComparison:
    scope = scope or Scope()
    pct = await percentile_of(session, tag, value, scope)
    nearest = await nearest_historical_runs(session, tag, value, scope, k=k)
    outcomes: dict[str, int] = {}
    for n in nearest:
        key = n.label or "unlabeled"
        outcomes[key] = outcomes.get(key, 0) + 1
    return DistributionComparison(
        percentile=pct, nearest_runs=nearest, nearest_outcomes=outcomes
    )


# ---------------------------------------------------------------------------
# Drift detection — Page-Hinkley on the daily-mean series
# ---------------------------------------------------------------------------

async def _fetch_daily_means(
    session: AsyncSession, tag: str, scope: Scope, days: int
) -> list[tuple[Any, float]]:
    where_clauses: list[str] = ["pr.start_time >= NOW() - (:days || ' days')::interval"]
    params: dict[str, Any] = {"tag": tag, "days": str(int(days))}
    if scope.line_id:
        where_clauses.append("pr.line_id = :line_id")
        params["line_id"] = scope.line_id
    if scope.product_style:
        where_clauses.append("pr.product_style = :product_style")
        params["product_style"] = scope.product_style
    if scope.equipment:
        # Soft equipment match against either run.metadata->>'equipment' or recipe.
        where_clauses.append(
            "COALESCE(pr.metadata->>'equipment', '') = :equipment"
        )
        params["equipment"] = scope.equipment

    sql = f"""
        SELECT
            date_trunc('day', pr.start_time) AS day,
            avg(
                COALESCE(
                    NULLIF(fs.features->>:tag, '')::float8,
                    NULLIF(fs.features->'tag_aggregates'->:tag->>'mean', '')::float8,
                    NULLIF(fs.features->:tag->>'mean', '')::float8
                )
            ) AS m
        FROM feature_snapshots fs
        JOIN production_runs   pr ON pr.id = fs.run_id
        WHERE {' AND '.join(where_clauses)}
        GROUP BY 1
        HAVING avg(
            COALESCE(
                NULLIF(fs.features->>:tag, '')::float8,
                NULLIF(fs.features->'tag_aggregates'->:tag->>'mean', '')::float8,
                NULLIF(fs.features->:tag->>'mean', '')::float8
            )
        ) IS NOT NULL
        ORDER BY 1
    """
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [(r["day"], float(r["m"])) for r in rows]


async def detect_drift(
    session: AsyncSession,
    tag: str,
    scope: Scope | None = None,
    days: int = 90,
    delta: float = 0.0,
    threshold: float = 4.0,
) -> DriftResult:
    """
    Page-Hinkley test on the daily-mean series. Conservative defaults; tune
    `threshold` per-tag once a baseline of false-positive rate is known.
    """
    scope = scope or Scope()
    series = await _fetch_daily_means(session, tag, scope, days)
    n = len(series)
    if n < 14:
        return DriftResult(
            tag=tag,
            scope=scope,
            drifted=False,
            statistic=0.0,
            threshold=threshold,
            sample_size=n,
            direction="none",
            window_label=f"last {days}d ({n} days observed)",
        )

    values = [v for _, v in series]
    cum_mean = 0.0
    m_t_up = 0.0   # tracks downward drift (cumulative deviation below mean)
    m_t_dn = 0.0   # tracks upward drift
    min_up = 0.0
    max_dn = 0.0
    direction: Literal["up", "down", "none"] = "none"

    for i, x in enumerate(values, start=1):
        cum_mean += (x - cum_mean) / i
        m_t_up += x - cum_mean - delta
        m_t_dn += x - cum_mean + delta
        min_up = min(min_up, m_t_up)
        max_dn = max(max_dn, m_t_dn)
        ph_up = m_t_up - min_up      # detects upward drift
        ph_dn = max_dn - m_t_dn      # detects downward drift
        stat = max(ph_up, ph_dn)
        if stat > threshold:
            direction = "up" if ph_up >= ph_dn else "down"
            return DriftResult(
                tag=tag,
                scope=scope,
                drifted=True,
                statistic=stat,
                threshold=threshold,
                sample_size=n,
                direction=direction,
                window_label=f"last {days}d (drift on day {i}/{n})",
            )

    final_stat = max(m_t_up - min_up, max_dn - m_t_dn)
    return DriftResult(
        tag=tag,
        scope=scope,
        drifted=False,
        statistic=final_stat,
        threshold=threshold,
        sample_size=n,
        direction="none",
        window_label=f"last {days}d ({n} days observed)",
    )


# ---------------------------------------------------------------------------
# Test seam — let unit tests inject sorted samples without DB.
# ---------------------------------------------------------------------------

def _seed_cache(tag: str, scope: Scope, values: Iterable[float],
                rows: list[dict[str, Any]] | None = None) -> None:
    sv = sorted(float(v) for v in values if math.isfinite(float(v)))
    _SAMPLE_CACHE[(tag, scope.cache_key())] = _CachedSamples(
        sorted_values=sv, rows=rows or [], fetched_at=time.time()
    )


def _clear_cache() -> None:
    _SAMPLE_CACHE.clear()
