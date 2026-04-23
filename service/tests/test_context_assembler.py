"""Tests for context_assembler. No DB required."""
from datetime import datetime, timezone

from models.schemas import (
    ActiveAlarm,
    CuratedContextPackage,
    RecipeContext,
    TagDeviation,
    TagSummaryStat,
    TagValue,
)
from services.context_assembler import (
    assemble_prompt,
    is_evidence_insufficient,
)


def _empty_curated():
    return CuratedContextPackage(
        snapshot_time=datetime.now(timezone.utc),
        line_id="coater1",
        key_tags=[],
        tag_summaries=[],
        deviations=[],
        active_alarms=[],
        recipe=None,
    )


def test_insufficient_evidence_when_everything_empty():
    out = assemble_prompt(
        user_query="Why is Coater 1 down?",
        curated=_empty_curated(),
        chunks=[], events=[], memories=[], rules=[],
    )
    assert is_evidence_insufficient(out.summary)


def test_evidence_present_when_tags_supplied():
    curated = CuratedContextPackage(
        snapshot_time=datetime.now(timezone.utc),
        line_id="coater1",
        key_tags=[TagValue(name="LineSpeed", value=245, unit="fpm", target=250)],
    )
    out = assemble_prompt(
        user_query="status?",
        curated=curated,
        chunks=[], events=[], memories=[], rules=[],
    )
    assert not is_evidence_insufficient(out.summary)
    assert out.summary["key_tags"] == 1
    assert any(c.type == "live_tag" for c in out.citations)


def test_section_delimiters_present():
    out = assemble_prompt(
        user_query="hi",
        curated=_empty_curated(),
        chunks=[], events=[], memories=[], rules=[],
    )
    for header in (
        "LIVE PLANT CONTEXT",
        "RECENT EVENTS",
        "RETRIEVED DOCUMENTS",
        "DETERMINISTIC RULES",
        "APPROVED LINE MEMORY",
        "ML PREDICTIONS",
        "USER QUESTION",
    ):
        assert "=== " + header in out.user_block


def test_citation_ids_are_unique_and_sequential():
    curated = CuratedContextPackage(
        snapshot_time=datetime.now(timezone.utc),
        line_id="coater1",
        key_tags=[
            TagValue(name="A", value=1.0),
            TagValue(name="B", value=2.0),
        ],
        tag_summaries=[
            TagSummaryStat(name="A", window_minutes=60, mean=1.0, current=1.0),
        ],
        deviations=[
            TagDeviation(name="A", current=5.0, baseline_mean=1.0, baseline_std=0.5,
                         sigma_deviation=8.0, direction="above"),
        ],
        active_alarms=[
            ActiveAlarm(source="a", priority="High", state="ActiveUnacked"),
        ],
        recipe=RecipeContext(product_style="X"),
    )
    out = assemble_prompt(
        user_query="status?",
        curated=curated,
        chunks=[], events=[], memories=[], rules=[],
    )
    ids = [int(c.id) for c in out.citations]
    assert ids == list(range(1, len(ids) + 1))
