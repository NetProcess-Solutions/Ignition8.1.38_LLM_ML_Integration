"""V2 anchor-conditional behavior tests for context_assembler."""
from datetime import datetime, timezone

from models.schemas import (
    ActiveAlarm,
    CuratedContextPackage,
    QueryAnchor,
    TagValue,
)
from services.context_assembler import assemble_prompt


def _curated_with_live_state(anchor):
    return CuratedContextPackage(
        snapshot_time=datetime.now(timezone.utc),
        line_id="coater1",
        key_tags=[TagValue(name="LineSpeed", value=245, unit="fpm", target=250)],
        active_alarms=[ActiveAlarm(source="x", priority="High", state="ActiveUnacked")],
        anchor=anchor,
    )


def test_past_event_excludes_live_tags_and_alarms():
    anchor = QueryAnchor(
        anchor_type="past_event",
        anchor_status="resolved",
        anchor_time=datetime(2024, 6, 14, 10, tzinfo=timezone.utc),
    )
    out = assemble_prompt(
        user_query="Why did the run yesterday delaminate?",
        curated=_curated_with_live_state(anchor),
        chunks=[], events=[], memories=[], rules=[],
    )
    assert "[NOT APPLICABLE" in out.user_block
    # live-state counts must be zeroed in summary
    assert out.summary["key_tags"] == 0
    assert out.summary["active_alarms"] == 0
    # No LIVE_TAG or ALARM citations
    assert not any(c.type == "LIVE_TAG" for c in out.citations)
    assert not any(c.type == "ALARM" for c in out.citations)
    # Bucket exclusions recorded
    excluded_names = {b.bucket for b in out.excluded_buckets}
    assert "live_tags" in excluded_names
    assert "live_alarms" in excluded_names


def test_current_state_includes_live_tags():
    anchor = QueryAnchor(anchor_type="current_state", anchor_status="resolved")
    out = assemble_prompt(
        user_query="Status now?",
        curated=_curated_with_live_state(anchor),
        chunks=[], events=[], memories=[], rules=[],
    )
    assert "[NOT APPLICABLE" not in out.user_block.split("=== LIVE TAG VALUES ===")[1].split("===")[0]
    assert out.summary["key_tags"] == 1
    assert any(c.type == "LIVE_TAG" for c in out.citations)


def test_pattern_excludes_live_state():
    anchor = QueryAnchor(anchor_type="pattern", anchor_status="resolved")
    out = assemble_prompt(
        user_query="How often does delamination happen on S-1234?",
        curated=_curated_with_live_state(anchor),
        chunks=[], events=[], memories=[], rules=[],
    )
    assert out.summary["key_tags"] == 0
    assert any(b.bucket == "live_tags" for b in out.excluded_buckets)
