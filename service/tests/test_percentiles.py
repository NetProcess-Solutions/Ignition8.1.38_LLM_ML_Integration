"""Sprint 3 / B0 — percentile_of / nearest_runs / drift unit tests.

Use the `_seed_cache` test seam to bypass the DB. Drift uses an in-memory
fake DB session via SQLAlchemy ORM-free `text()` results -- monkeypatched.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services import percentiles
from services.percentiles import (
    Scope,
    compare_to_distribution,
    nearest_historical_runs,
    percentile_of,
)


@pytest.fixture(autouse=True)
def _clear() -> None:
    percentiles._clear_cache()
    yield
    percentiles._clear_cache()


@pytest.mark.asyncio
async def test_percentile_uniform_distribution() -> None:
    scope = Scope(kind="style", product_style="S-1234")
    percentiles._seed_cache("Front2_Temp", scope, list(range(0, 100)))
    res = await percentile_of(session=None, tag="Front2_Temp", value=50, scope=scope)  # type: ignore[arg-type]
    assert res.sample_size == 100
    # 50 is greater than [0..50], so percentile = 51/100
    assert 0.5 <= res.percentile <= 0.55
    assert res.interpretation == "typical"


@pytest.mark.asyncio
async def test_percentile_extremes() -> None:
    scope = Scope()
    percentiles._seed_cache("X", scope, [10] * 100 + [11, 12])
    low = await percentile_of(session=None, tag="X", value=5, scope=scope)  # type: ignore[arg-type]
    assert low.percentile == 0.0
    assert low.interpretation == "very_low"
    high = await percentile_of(session=None, tag="X", value=20, scope=scope)  # type: ignore[arg-type]
    assert high.percentile == 1.0
    assert high.interpretation == "very_high"


@pytest.mark.asyncio
async def test_percentile_no_samples_returns_unknown() -> None:
    scope = Scope(kind="style", product_style="S-9999")
    percentiles._seed_cache("Missing_Tag", scope, [])
    res = await percentile_of(session=None, tag="Missing_Tag", value=42, scope=scope)  # type: ignore[arg-type]
    assert res.sample_size == 0
    assert res.percentile is None
    assert res.interpretation == "unknown"


@pytest.mark.asyncio
async def test_nearest_runs_returns_closest_with_outcome_labels() -> None:
    scope = Scope(kind="style", product_style="S-1234")
    rows = [
        {"run_id": "r-100", "run_number": "R-20260101-01", "product_style": "S-1234",
         "front_step": 2, "start_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
         "v": 198.0, "label": "delam_hotpull"},
        {"run_id": "r-101", "run_number": "R-20260102-01", "product_style": "S-1234",
         "front_step": 2, "start_time": datetime(2026, 1, 2, tzinfo=timezone.utc),
         "v": 198.5, "label": "ok"},
        {"run_id": "r-102", "run_number": "R-20260103-01", "product_style": "S-1234",
         "front_step": 2, "start_time": datetime(2026, 1, 3, tzinfo=timezone.utc),
         "v": 250.0, "label": "ok"},
    ]
    percentiles._seed_cache("Front2_Temp", scope, [r["v"] for r in rows], rows=rows)
    runs = await nearest_historical_runs(
        session=None, tag="Front2_Temp", value=199.0, scope=scope, k=2  # type: ignore[arg-type]
    )
    assert len(runs) == 2
    assert {r.run_id for r in runs} == {"r-100", "r-101"}
    cmp = await compare_to_distribution(
        session=None, tag="Front2_Temp", value=199.0, scope=scope, k=2  # type: ignore[arg-type]
    )
    assert cmp.nearest_outcomes == {"delam_hotpull": 1, "ok": 1}


def test_scope_cache_key_distinguishes() -> None:
    a = Scope(kind="style", product_style="S-1")
    b = Scope(kind="style", product_style="S-2")
    assert a.cache_key() != b.cache_key()
    assert a.describe() != b.describe()
