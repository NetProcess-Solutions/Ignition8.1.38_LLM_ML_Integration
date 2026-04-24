"""Sprint 6 / B3 — structure-aware chunker tests."""
from __future__ import annotations

from services.chunker import StructuredChunk, chunk_structured


def test_falls_back_to_size_chunker_when_no_headings():
    s = "plain prose " * 5
    out = chunk_structured(s)
    assert len(out) == 1
    assert out[0].section_path == []
    assert out[0].parent_heading_id is None


def test_splits_on_markdown_headings_and_tracks_path():
    doc = (
        "# Intro\n"
        "Background paragraph.\n\n"
        "# Failure 4521\n"
        "Top-level summary line.\n\n"
        "## Symptoms\n"
        "Symptom paragraph.\n\n"
        "## Resolution\n"
        "Resolution paragraph.\n"
    )
    out = chunk_structured(doc)
    paths = [c.section_path for c in out]
    assert ["Intro"] in paths
    assert ["Failure 4521"] in paths
    assert ["Failure 4521", "Symptoms"] in paths
    assert ["Failure 4521", "Resolution"] in paths


def test_parent_heading_id_is_stable_within_section():
    doc = "# A\n" + ("body line\n" * 50)
    out = chunk_structured(doc)
    # All chunks under heading A share the same parent_heading_id
    hids = {c.parent_heading_id for c in out}
    assert len(hids) == 1


def test_caps_all_caps_loose_heading_detection():
    doc = (
        "PROCEDURE:\n"
        "Step 1 do thing.\n"
        "Step 2 do other thing.\n\n"
        "RESOLUTION:\n"
        "Done.\n"
    )
    out = chunk_structured(doc)
    paths = [c.section_path for c in out]
    assert ["PROCEDURE"] in paths
    assert ["RESOLUTION"] in paths


def test_metadata_returns_dict():
    sc = StructuredChunk(text="x", section_path=["A", "B"], parent_heading_id="h_abc")
    md = sc.metadata()
    assert md["section_path"] == ["A", "B"]
    assert md["parent_heading_id"] == "h_abc"


def test_empty_input_returns_empty_list():
    assert chunk_structured("") == []
    assert chunk_structured("   \n\n  ") == []
