"""Sprint 5 / B1 — Hybrid retrieval pure-function tests.

We test the deterministic pieces (`_rrf_fuse`, `_mmr_select`,
`_keyword_terms`, `_conditional_boost`) in isolation. The DB-touching
parts are exercised by integration tests separately.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from services.retrieval import RetrievedChunk
from services.retrieval import (
    _conditional_boost,
    _keyword_terms,
    _mmr_select,
    _rrf_fuse,
)


def _chunk(text: str, similarity: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        chunk_text=text,
        similarity=similarity,
        quality_signal=1.0,
        document_weight=1.0,
        blended_score=similarity,
        document_title="t",
        document_date=None,
        source_type="document",
        document_role=None,
        metadata={},
    )


def test_keyword_terms_drops_stopwords_and_dedupes():
    terms = _keyword_terms("Why did the coater have a sag at line 1?")
    # Stopwords ('why', 'did', 'the', 'a', 'at') removed; coater kept.
    assert "coater" in terms
    assert "sag" in terms
    assert "the" not in terms
    assert "why" not in terms
    # No duplicates and stable enough length.
    assert len(set(terms)) == len(terms)


def test_keyword_terms_handles_empty_query():
    assert _keyword_terms("") == []
    assert _keyword_terms("   ") == []


def test_rrf_fuse_promotes_chunks_present_in_both_lists():
    a = _chunk("foo")
    b = _chunk("bar")
    c = _chunk("baz")
    # 'a' is rank-1 in vector list and rank-1 in keyword list -> highest RRF.
    fused = _rrf_fuse([a, b], [a, c], k_rrf=60)
    assert str(fused[0].chunk_id) == str(a.chunk_id)
    # All three end up in the fused list.
    assert {str(x.chunk_id) for x in fused} == {
        str(a.chunk_id), str(b.chunk_id), str(c.chunk_id),
    }
    # Score is monotone — first > rest.
    assert fused[0].blended_score >= fused[1].blended_score >= fused[2].blended_score


def test_rrf_fuse_handles_single_list():
    a = _chunk("foo")
    fused = _rrf_fuse([a], k_rrf=60)
    assert len(fused) == 1


def test_mmr_select_diversifies_near_duplicates():
    # Two near-identical chunks plus one diverse one. MMR should prefer
    # the diverse chunk for the second slot.
    a = _chunk("the coater roller bearing failed at 14:00 yesterday on line 1", 0.9)
    b = _chunk("the coater roller bearing failed at 14:01 yesterday on line 1", 0.85)
    c = _chunk("downtime caused by polymer pump motor overheat condition", 0.7)
    out = _mmr_select([a, b, c], top_k=2, lambda_mult=0.5)
    out_ids = {str(x.chunk_id) for x in out}
    assert str(a.chunk_id) in out_ids
    # MMR with lambda=0.5 must choose c over b for the second slot.
    assert str(c.chunk_id) in out_ids


def test_mmr_select_returns_input_when_smaller_than_top_k():
    a = _chunk("only one")
    out = _mmr_select([a], top_k=5)
    assert out == [a]


def test_conditional_boost_failure_mode_match():
    base = _chunk("text")
    base.metadata = {"doc": {"failure_mode": "sag"}}
    base.blended_score = 1.0
    _conditional_boost(
        [base], failure_mode="sag", equipment=None,
        fm_boost=1.5, equip_boost=1.3,
    )
    assert base.blended_score == pytest.approx(1.5)


def test_conditional_boost_no_match_is_noop():
    base = _chunk("text")
    base.metadata = {"doc": {"failure_mode": "drift"}}
    base.blended_score = 1.0
    _conditional_boost(
        [base], failure_mode="sag", equipment=None,
        fm_boost=1.5, equip_boost=1.3,
    )
    assert base.blended_score == pytest.approx(1.0)


def test_conditional_boost_equipment_match_compounds_with_failure_mode():
    base = _chunk("text")
    base.metadata = {
        "doc": {"failure_mode": "sag", "equipment_id": "coater_1"},
    }
    base.blended_score = 1.0
    _conditional_boost(
        [base], failure_mode="sag", equipment=["coater_1"],
        fm_boost=1.5, equip_boost=1.3,
    )
    # Both boosts compound multiplicatively.
    assert base.blended_score == pytest.approx(1.5 * 1.3)
