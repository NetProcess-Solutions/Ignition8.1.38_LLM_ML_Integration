"""Smoke tests for v2.0 integrations: ensure modules import and helpers
behave deterministically without requiring live DBs or cameras."""
from datetime import datetime, timedelta, timezone

import pytest

import services.symphony_capture as sc
import services.wo_sync as wo


def test_symphony_camera_lookup_uses_overview_when_unknown():
    cams = sc._cameras_for("nonexistent.equipment.id")
    # Falls back to coater1.line_general overview camera
    assert cams == ["cam-c1-overview"]


def test_symphony_camera_lookup_returns_specific_for_known_equipment():
    cams = sc._cameras_for("coater1.zone3")
    assert "cam-c1-z3-01" in cams
    assert "cam-c1-z3-02" in cams


def test_symphony_request_clip_returns_handle_with_window_in_path():
    start = datetime(2024, 6, 15, 10, 0, tzinfo=timezone.utc)
    end = datetime(2024, 6, 15, 10, 1, tzinfo=timezone.utc)
    clip = sc._request_clip("cam-c1-app-01", start, end)
    assert "cam-c1-app-01" in clip["storage_handle"]
    assert clip["extraction_status"] == "stub"


def test_wo_narrative_includes_problem_and_resolution():
    row = wo.WorkOrderRow(
        wo_number="WO-12345",
        line_id="coater1",
        equipment_id="coater1.applicator",
        wo_type="corrective",
        problem_description="Applicator pump cavitating",
        resolution_notes="Replaced impeller and checked alignment",
        date_opened=datetime(2024, 6, 10, tzinfo=timezone.utc),
        date_closed=datetime(2024, 6, 11, tzinfo=timezone.utc),
        technician="J. Doe",
    )
    text_body = wo._wo_narrative(row)
    assert "WO-12345" in text_body
    assert "cavitating" in text_body
    assert "Replaced impeller" in text_body
    assert "Problem:" in text_body and "Resolution:" in text_body


def test_wo_sync_raises_when_db_url_missing(monkeypatch):
    """Configuration error must be loud, not silent."""
    from config import settings as settings_mod

    real_get = settings_mod.get_settings
    def fake_get():
        s = real_get()
        s.ignition_wo_db_url = ""
        return s
    monkeypatch.setattr(settings_mod, "get_settings", fake_get)
    monkeypatch.setattr(wo, "get_settings", fake_get, raising=False)

    async def _run():
        await wo._fetch_recent_from_ignition(datetime.now(timezone.utc) - timedelta(days=1))

    import asyncio
    with pytest.raises(RuntimeError, match="ignition_wo_db_url"):
        asyncio.new_event_loop().run_until_complete(_run())
