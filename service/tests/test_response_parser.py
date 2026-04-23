"""Pure unit tests for the response parser."""
from services.response_parser import (
    extract_cited_ids,
    has_any_citations,
    parse_confidence,
)


def test_parse_confidence_confirmed():
    assert parse_confidence("Some text\nCONFIDENCE: CONFIRMED") == "confirmed"


def test_parse_confidence_likely():
    assert parse_confidence("...\nconfidence: likely") == "likely"


def test_parse_confidence_hypothesis():
    assert parse_confidence("CONFIDENCE: HYPOTHESIS") == "hypothesis"


def test_parse_confidence_insufficient_underscore():
    assert parse_confidence("CONFIDENCE: INSUFFICIENT_EVIDENCE") == "insufficient_evidence"


def test_parse_confidence_insufficient_space():
    assert parse_confidence("CONFIDENCE: INSUFFICIENT EVIDENCE") == "insufficient_evidence"


def test_parse_confidence_missing_defaults_hypothesis():
    assert parse_confidence("no label here") == "hypothesis"


def test_extract_citations():
    text = "Foo [1] bar [3] baz [1] qux [42]"
    assert extract_cited_ids(text) == {"1", "3", "42"}


def test_has_any_citations_true():
    assert has_any_citations("answer with [5]")


def test_has_any_citations_false():
    assert not has_any_citations("answer without any cites")
