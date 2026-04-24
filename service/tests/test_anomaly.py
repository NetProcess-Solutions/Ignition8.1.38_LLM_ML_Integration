"""Sprint 5 / B7 — multivariate anomaly Mahalanobis fit unit tests."""
from __future__ import annotations

import numpy as np
import pytest

from services.anomaly import _fit, score_live_snapshot


def _gen_normal(n: int, mean: list[float], cov: list[list[float]],
                seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.multivariate_normal(mean, cov, size=n)


def test_fit_returns_none_when_too_few_samples():
    matrix = np.array([[1.0, 2.0], [3.0, 4.0]])
    assert _fit(matrix, ["a", "b"]) is None  # n=2 < d+5=7


def test_fit_recovers_mean_and_threshold():
    matrix = _gen_normal(500, mean=[0.0, 0.0], cov=[[1.0, 0.0], [0.0, 1.0]])
    model = _fit(matrix, ["a", "b"])
    assert model is not None
    assert model.sample_size == 500
    assert np.allclose(model.mean, [0.0, 0.0], atol=0.15)
    # 95th-percentile of Mahalanobis from the training set itself.
    assert 1.5 < model.threshold < 4.0


def test_fit_handles_singular_covariance_via_ridge():
    # Two perfectly-correlated columns -> singular covariance. Ridge
    # should still permit inversion.
    base = _gen_normal(100, mean=[0.0], cov=[[1.0]])
    matrix = np.hstack([base, base])
    model = _fit(matrix, ["a", "b"])
    assert model is not None
    # Inverse should be finite.
    assert np.isfinite(model.inv_cov).all()


@pytest.mark.asyncio
async def test_score_returns_none_when_anomaly_disabled(monkeypatch):
    from config.settings import get_settings
    monkeypatch.setattr(get_settings(), "anomaly_enabled", False)
    out = await score_live_snapshot(
        session=None,  # type: ignore[arg-type]
        line_id="L1", style="A", front_step=1,
        current_tags={"x": 1.0, "y": 2.0},
    )
    assert out is None


@pytest.mark.asyncio
async def test_score_returns_none_for_under_2_tags():
    out = await score_live_snapshot(
        session=None,  # type: ignore[arg-type]
        line_id="L1", style="A", front_step=1,
        current_tags={"x": 1.0},
    )
    assert out is None
