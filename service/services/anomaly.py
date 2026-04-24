"""
Sprint 5 / B7 — Multivariate anomaly detection per (style, front_step).

Mahalanobis distance against a per-cluster mean+covariance fit on the
historical feature_snapshots vectors. Adds a `multivariate_anomaly`
deviation when the live snapshot's distance is in the top decile of the
training distribution.

Pure numpy. No sklearn required (one less binary dep on the gateway path
deployment). Models are fit lazily and cached in-process per scope.

Why this is distinct from the per-tag deviation we already compute:
catches the "no single tag is bad but the joint state is unusual"
pattern that drives early-stage hot-pull setup defects.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings


@dataclass
class AnomalyResult:
    score: float                # Mahalanobis distance (sqrt of squared)
    threshold: float            # p95 of training distribution
    is_anomaly: bool
    sample_size: int
    contributing_tags: list[str]  # top-K tags by per-dim |z|


@dataclass
class _FittedModel:
    tag_order: list[str]
    mean: np.ndarray            # (D,)
    inv_cov: np.ndarray         # (D, D)
    threshold: float
    sample_size: int


_MODEL_CACHE: dict[str, _FittedModel] = {}


def _scope_key(line_id: str, style: str, front_step: int | None) -> str:
    return f"{line_id}|{style}|{front_step}"


async def _fetch_history_matrix(
    session: AsyncSession, line_id: str, style: str, front_step: int | None,
    tags: list[str],
) -> np.ndarray:
    """Return (N, D) matrix of historical per-tag means."""
    where = ["pr.line_id = :line", "pr.product_style = :style"]
    params: dict[str, Any] = {"line": line_id, "style": style}
    if front_step is not None:
        where.append("pr.front_step = :fs")
        params["fs"] = front_step

    select_exprs = ", ".join(
        f"""
        COALESCE(
            NULLIF(fs.features->>:tag_{i}, '')::float8,
            NULLIF(fs.features->'tag_aggregates'->:tag_{i}->>'mean', '')::float8,
            NULLIF(fs.features->:tag_{i}->>'mean', '')::float8
        ) AS v_{i}
        """
        for i in range(len(tags))
    )
    for i, t in enumerate(tags):
        params[f"tag_{i}"] = t

    sql = text(f"""
        SELECT {select_exprs}
        FROM feature_snapshots fs
        JOIN production_runs pr ON pr.id = fs.run_id
        WHERE {' AND '.join(where)}
        ORDER BY pr.start_time DESC
        LIMIT 5000
    """)
    rows = (await session.execute(sql, params)).all()
    if not rows:
        return np.empty((0, len(tags)))
    arr = np.array(rows, dtype=float)
    # Drop rows with any NaN.
    mask = ~np.isnan(arr).any(axis=1)
    return arr[mask]


def _fit(matrix: np.ndarray, tag_order: list[str]) -> _FittedModel | None:
    n, d = matrix.shape
    if n < d + 5:
        return None
    mean = matrix.mean(axis=0)
    cov = np.cov(matrix, rowvar=False)
    if d == 1:
        cov = np.array([[float(cov)]])
    # Ridge for numerical stability.
    cov += np.eye(d) * 1e-6 * np.trace(cov) / max(d, 1)
    try:
        inv_cov = np.linalg.inv(cov)
    except np.linalg.LinAlgError:
        return None
    diffs = matrix - mean
    md_sq = np.einsum("ij,jk,ik->i", diffs, inv_cov, diffs)
    md = np.sqrt(np.clip(md_sq, 0, None))
    threshold = float(np.percentile(md, 95))
    return _FittedModel(
        tag_order=tag_order, mean=mean, inv_cov=inv_cov,
        threshold=threshold, sample_size=int(n),
    )


async def get_or_fit_model(
    session: AsyncSession, *, line_id: str, style: str,
    front_step: int | None, tags: list[str],
) -> _FittedModel | None:
    s = get_settings()
    key = _scope_key(line_id, style, front_step) + "|" + ",".join(sorted(tags))
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    matrix = await _fetch_history_matrix(session, line_id, style, front_step, tags)
    if matrix.shape[0] < s.anomaly_min_history_runs:
        return None
    model = _fit(matrix, tags)
    if model is not None:
        _MODEL_CACHE[key] = model
    return model


async def score_live_snapshot(
    session: AsyncSession, *, line_id: str, style: str,
    front_step: int | None, current_tags: dict[str, float],
    top_contributing: int = 3,
) -> AnomalyResult | None:
    """Score a live tag snapshot. Returns None if no fittable history."""
    s = get_settings()
    if not s.anomaly_enabled:
        return None
    tags = sorted(t for t, v in current_tags.items() if v is not None)
    if len(tags) < 2:
        return None
    model = await get_or_fit_model(
        session, line_id=line_id, style=style,
        front_step=front_step, tags=tags,
    )
    if model is None:
        return None

    x = np.array([float(current_tags[t]) for t in model.tag_order])
    diff = x - model.mean
    md = float(np.sqrt(max(0.0, diff @ model.inv_cov @ diff)))
    # Per-dim absolute z-score for explainability.
    diag = np.diag(model.inv_cov)
    diag_safe = np.where(diag > 0, diag, np.nan)
    z_per_dim = np.abs(diff) * np.sqrt(diag_safe)
    order = np.argsort(np.where(np.isnan(z_per_dim), -1, z_per_dim))[::-1]
    contributing = [model.tag_order[i] for i in order[:top_contributing]]

    return AnomalyResult(
        score=md,
        threshold=model.threshold,
        is_anomaly=md > model.threshold,
        sample_size=model.sample_size,
        contributing_tags=contributing,
    )


def _clear_cache() -> None:
    _MODEL_CACHE.clear()
