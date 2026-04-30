"""Unit tests for the audit hash chain (TDD §14).

These exercise the pure functions in `services.audit` without a DB:
- canonical_json determinism (key order, separators)
- compute_audit_hash determinism + chaining
- A tampered payload anywhere in the chain causes downstream verification
  to diverge.
"""
from __future__ import annotations

from services.audit import canonical_json, compute_audit_hash


def test_canonical_json_sorts_keys():
    a = canonical_json({"b": 1, "a": 2})
    b = canonical_json({"a": 2, "b": 1})
    assert a == b == '{"a":2,"b":1}'


def test_canonical_json_minimal_separators():
    out = canonical_json({"x": [1, 2], "y": {"z": "q"}})
    assert " " not in out  # no whitespace separators


def test_canonical_json_handles_uuid_and_datetime():
    from datetime import datetime
    from uuid import UUID
    payload = {
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "ts": datetime(2026, 4, 30, 12, 34, 56),
    }
    out = canonical_json(payload)
    assert "00000000-0000-0000-0000-000000000001" in out
    assert "2026-04-30T12:34:56" in out


def test_compute_audit_hash_deterministic():
    payload = {"event_type": "chat_query", "user_id": "u1", "details": {"x": 1}}
    h1 = compute_audit_hash(None, payload)
    h2 = compute_audit_hash(None, payload)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_compute_audit_hash_chains():
    """Same payload but different prev_hash -> different result."""
    p = {"event_type": "x"}
    h_no_prev = compute_audit_hash(None, p)
    h_with_prev = compute_audit_hash(h_no_prev, p)
    assert h_no_prev != h_with_prev


def test_compute_audit_hash_empty_string_equals_none():
    """Convention: empty-string prev is equivalent to None (first row)."""
    p = {"event_type": "x"}
    assert compute_audit_hash(None, p) == compute_audit_hash("", p)


def test_chain_break_detected():
    """Simulate a 3-row chain; tamper row 2; row 3 verification diverges."""
    payloads = [
        {"event_type": "a", "i": 0},
        {"event_type": "b", "i": 1},
        {"event_type": "c", "i": 2},
    ]
    chain: list[str] = []
    prev: str | None = None
    for p in payloads:
        h = compute_audit_hash(prev, p)
        chain.append(h)
        prev = h

    # Tamper row 1 payload (index 1)
    tampered = dict(payloads[1])
    tampered["i"] = 999
    # Recompute row 1 with same prev (chain[0]) -> different hash
    h1_new = compute_audit_hash(chain[0], tampered)
    assert h1_new != chain[1]

    # Row 2 recomputed from the tampered row's hash also diverges
    h2_new = compute_audit_hash(h1_new, payloads[2])
    assert h2_new != chain[2]
