"""Sprint 5 / B9 — change-ledger pure-function tests."""
from __future__ import annotations

import pytest

from services.baseline_cache import TagBaseline
from services.change_ledger import (
    CrewDelta,
    EquipmentChangeover,
    RecipeDelta,
    TagDelta,
    ChangeLedger,
    _tag_delta,
    compute_tag_deltas,
)


def _baseline(mean: float, std: float) -> TagBaseline:
    tb = TagBaseline(tag_name="t")
    tb.mean = mean
    tb.std = std
    return tb


def test_tag_delta_above_threshold_yields_delta():
    d = _tag_delta("temp", 110.0, _baseline(100.0, 5.0), near_sigma=1.0)
    assert d is not None
    assert d.direction == "above"
    assert d.sigma == pytest.approx(2.0)


def test_tag_delta_within_sigma_band_returns_none():
    assert _tag_delta("temp", 100.5, _baseline(100.0, 5.0), near_sigma=1.0) is None


def test_tag_delta_below_threshold_yields_below():
    d = _tag_delta("temp", 80.0, _baseline(100.0, 5.0), near_sigma=1.0)
    assert d is not None
    assert d.direction == "below"
    assert d.sigma == pytest.approx(-4.0)


def test_tag_delta_handles_zero_std():
    # std == 0 should not divide by zero; treat as a tiny epsilon → enormous sigma
    d = _tag_delta("t", 1.0, _baseline(0.0, 0.0))
    assert d is not None and d.direction == "above"


def test_compute_tag_deltas_sorts_by_abs_sigma_and_caps():
    baselines = {
        "a": _baseline(0.0, 1.0),
        "b": _baseline(0.0, 1.0),
        "c": _baseline(0.0, 1.0),
        "d": _baseline(0.0, 1.0),
    }
    current = {"a": 5.0, "b": -3.0, "c": 2.0, "d": -7.0}
    out = compute_tag_deltas(current, baselines, top_k=2)
    assert [d.tag_name for d in out] == ["d", "a"]


def test_compute_tag_deltas_skips_unknown_or_none():
    baselines = {"a": _baseline(0.0, 1.0)}
    out = compute_tag_deltas(
        {"a": None, "b": 99.0, "c": 5.0}, baselines, top_k=10,
    )
    # Only 'c' has both a value and a baseline... but no baseline for c.
    # 'a' has a baseline but no value, 'b' has no baseline.
    assert out == []


def test_change_ledger_is_empty_property():
    cl = ChangeLedger()
    assert cl.is_empty is True
    cl.tag_deltas = [TagDelta("t", 1.0, 0.0, 1.0, 1.0, "above")]
    assert cl.is_empty is False


def test_change_ledger_serialization_round_trip_shapes():
    cl = ChangeLedger(
        tag_deltas=[TagDelta("t", 1.0, 0.0, 1.0, 1.0, "above")],
        recipe_deltas=[RecipeDelta("recipe_id", "X", "Y", "note")],
        crew_delta=CrewDelta("c1", "n", 0.1, "rare"),
        equipment_changeovers=[EquipmentChangeover("eq", "WO1", None, "summary")],
    )
    d = cl.as_dict()
    assert set(d.keys()) == {
        "tag_deltas", "recipe_deltas", "crew_delta", "equipment_changeovers",
    }
    assert d["tag_deltas"][0]["tag"] == "t"
    assert d["crew_delta"]["crew"] == "c1"
