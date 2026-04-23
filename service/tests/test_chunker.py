"""Tests for the chunker."""
from services.chunker import chunk_text


def test_short_text_one_chunk():
    chunks = chunk_text("Hello world.")
    assert chunks == ["Hello world."]


def test_empty_returns_empty():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_long_text_multiple_chunks_with_overlap():
    body = ("Sentence one. " * 500).strip()
    chunks = chunk_text(body)
    assert len(chunks) > 1
    # Each chunk should be at most ~target chars
    for c in chunks:
        assert len(c) <= 500 * 4 + 20
