"""Sprint 1 / A7 — Anchor regression test driven by YAML fixtures.

Add or change fixtures in `fixtures/anchor_regression.yaml`; this test
will pick them up automatically. Each row asserts only the fields it
declares, so it's safe to specify partial expectations.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from services.anchor import resolve_anchor


_FIXTURE = Path(__file__).parent / "fixtures" / "anchor_regression.yaml"
_NOW_DEFAULT = datetime(2024, 6, 15, 14, 30, tzinfo=timezone.utc)


def _load() -> list[dict[str, Any]]:
    rows = yaml.safe_load(_FIXTURE.read_text()) or []
    assert isinstance(rows, list), f"{_FIXTURE} must be a YAML list"
    return rows


_ROWS = _load()


@pytest.mark.parametrize(
    "row", _ROWS, ids=[r.get("id", f"row_{i}") for i, r in enumerate(_ROWS)]
)
def test_anchor_regression(row: dict[str, Any]) -> None:
    query = row["query"]
    now_iso = row.get("now")
    now = (
        datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        if now_iso
        else _NOW_DEFAULT
    )
    expect = row.get("expect", {}) or {}
    a = resolve_anchor(query, now=now)

    if "anchor_type" in expect:
        assert a.anchor_type == expect["anchor_type"], (
            f"{row['id']}: anchor_type {a.anchor_type!r} != {expect['anchor_type']!r}"
        )
    if "anchor_status" in expect:
        assert a.anchor_status == expect["anchor_status"], (
            f"{row['id']}: anchor_status {a.anchor_status!r} != {expect['anchor_status']!r}"
        )
    if "anchor_status_in" in expect:
        assert a.anchor_status in expect["anchor_status_in"], (
            f"{row['id']}: anchor_status {a.anchor_status!r} not in {expect['anchor_status_in']}"
        )
    if "anchor_run_id" in expect:
        assert a.anchor_run_id == expect["anchor_run_id"]
    if "failure_mode_scope" in expect:
        assert a.failure_mode_scope == expect["failure_mode_scope"]
    if "style_scope" in expect:
        assert a.style_scope == expect["style_scope"]
    if "equipment_scope" in expect:
        # equipment_scope is a list on QueryAnchor; allow membership match.
        scope = a.equipment_scope or []
        expected = expect["equipment_scope"]
        if isinstance(expected, str):
            assert expected in scope, (
                f"{row['id']}: expected {expected!r} in equipment_scope {scope!r}"
            )
        else:
            assert set(expected).issubset(set(scope))
    if "anchor_date" in expect:
        assert a.anchor_time is not None, f"{row['id']}: expected anchor_time, got None"
        assert a.anchor_time.date() == date.fromisoformat(expect["anchor_date"])
