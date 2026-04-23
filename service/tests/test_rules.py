"""Tests for the deterministic rule evaluator. No DB required - we test the
clause evaluator directly."""
from services.rules import _eval_clause


def test_gt_match():
    ok, _ = _eval_clause({"tag": "T", "op": ">", "value": 10}, {"T": 15})
    assert ok


def test_gt_no_match():
    ok, _ = _eval_clause({"tag": "T", "op": ">", "value": 10}, {"T": 5})
    assert not ok


def test_lt_match():
    ok, _ = _eval_clause({"tag": "T", "op": "<", "value": 10}, {"T": 5})
    assert ok


def test_eq_string():
    ok, _ = _eval_clause({"tag": "S", "op": "==", "value": "running"},
                          {"S": "running"})
    assert ok


def test_missing_tag_no_match():
    ok, _ = _eval_clause({"tag": "X", "op": ">", "value": 0}, {"Y": 5})
    assert not ok


def test_invalid_op():
    ok, msg = _eval_clause({"tag": "T", "op": "foo", "value": 0}, {"T": 1})
    assert not ok
    assert "invalid" in msg
