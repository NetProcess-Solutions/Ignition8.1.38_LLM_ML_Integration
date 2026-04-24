"""Sprint 4 / B8 — RCA chain unit tests (router + JSON parsing + cache)."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from services import rca


def _anchor(**kw):
    base = dict(
        anchor_type="past_event",
        anchor_event_id=str(uuid4()),
        anchor_run_id=None,
        anchor_time=datetime.now(timezone.utc),
        failure_mode_scope="sag",
        style_scope="A1",
        equipment_scope=[],
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class TestShouldUseRcaChain:
    def test_triggers_on_why_with_past_event_and_failure_mode(self):
        assert rca.should_use_rca_chain(
            "Why did the sag happen yesterday?", _anchor()
        )

    def test_triggers_on_root_cause_phrase(self):
        assert rca.should_use_rca_chain(
            "What was the root cause of that downtime?", _anchor()
        )

    def test_does_not_trigger_on_current_state_anchor(self):
        a = _anchor(anchor_type="current_state")
        assert not rca.should_use_rca_chain("Why is the line slow?", a)

    def test_does_not_trigger_without_event_or_failure_mode(self):
        a = _anchor(anchor_event_id=None, failure_mode_scope=None)
        assert not rca.should_use_rca_chain("Why did it stop?", a)

    def test_does_not_trigger_on_descriptive_query(self):
        # "What is" is not causal intent
        assert not rca.should_use_rca_chain(
            "What is the typical sag rate at this style?", _anchor()
        )

    def test_respects_settings_flag(self, monkeypatch):
        from config.settings import get_settings
        s = get_settings()
        original = s.rca_chain_enabled
        try:
            monkeypatch.setattr(s, "rca_chain_enabled", False)
            assert not rca.should_use_rca_chain(
                "Why did the sag happen?", _anchor()
            )
        finally:
            monkeypatch.setattr(s, "rca_chain_enabled", original)


# ---------------------------------------------------------------------------
# Step 1 JSON parsing
# ---------------------------------------------------------------------------

class TestParseHypotheses:
    def test_parses_well_formed_json_object(self):
        blob = """{
            "hypotheses": [
              {"cause_label": "polymer drift",
               "rationale_short": "viscosity above baseline",
               "evidence_already_seen": ["3"],
               "evidence_to_gather": [
                  {"kind": "percentile",
                   "arguments": {"tag_name":"visc"},
                   "rationale":"check distribution"}
               ],
               "prior_probability": 0.6}
            ]}"""
        out = rca._parse_hypotheses(blob, cap=5)
        assert len(out) == 1
        assert out[0].cause_label == "polymer drift"
        assert out[0].evidence_to_gather[0].kind == "percentile"

    def test_strips_code_fences(self):
        blob = "```json\n{\"hypotheses\": []}\n```"
        out = rca._parse_hypotheses(blob, cap=5)
        assert out == []

    def test_extracts_brace_block_when_text_before(self):
        blob = "Sure, here is my answer: {\"hypotheses\": []}"
        out = rca._parse_hypotheses(blob, cap=5)
        assert out == []

    def test_returns_empty_on_garbage(self):
        assert rca._parse_hypotheses("not json at all", cap=5) == []
        assert rca._parse_hypotheses("", cap=5) == []
        assert rca._parse_hypotheses("{not closed", cap=5) == []

    def test_caps_returned_hypotheses(self):
        items = [
            {"cause_label": f"h{i}", "rationale_short": "r",
             "evidence_already_seen": [], "evidence_to_gather": [],
             "prior_probability": 0.5}
            for i in range(10)
        ]
        blob = '{"hypotheses": ' + str(items).replace("'", '"') + "}"
        out = rca._parse_hypotheses(blob, cap=3)
        assert len(out) == 3

    def test_silently_skips_invalid_hypothesis_items(self):
        blob = """{"hypotheses": [
            {"cause_label": "ok", "rationale_short": "x",
             "evidence_to_gather": [], "prior_probability": 0.5},
            {"missing_required_fields": true}
        ]}"""
        out = rca._parse_hypotheses(blob, cap=5)
        # First parses, second skipped silently.
        assert len(out) == 1
        assert out[0].cause_label == "ok"


# ---------------------------------------------------------------------------
# Step 2 confidence parsing
# ---------------------------------------------------------------------------

class TestParseStep2Confidence:
    @pytest.mark.parametrize(
        "label_in,label_out",
        [
            ("CONFIRMED", "confirmed"),
            ("LIKELY", "likely"),
            ("HYPOTHESIS", "hypothesis"),
            ("INSUFFICIENT_EVIDENCE", "insufficient_evidence"),
        ],
    )
    def test_parses_each_confidence_level(self, label_in, label_out):
        text = f"... blah blah ...\n\nCONFIDENCE: {label_in}"
        assert rca._parse_step2_confidence(text) == label_out

    def test_defaults_to_hypothesis_when_missing(self):
        assert rca._parse_step2_confidence("no confidence here") == "hypothesis"

    def test_case_insensitive(self):
        assert rca._parse_step2_confidence(
            "confidence: confirmed"
        ) == "confirmed"


# ---------------------------------------------------------------------------
# In-process cache
# ---------------------------------------------------------------------------

class TestRcaCache:
    def setup_method(self):
        rca._clear_cache()

    def test_round_trip_cache(self):
        anchor = _anchor()
        from models.schemas import RcaHypothesis
        hyps = [RcaHypothesis(
            cause_label="x", rationale_short="r",
            evidence_to_gather=[], prior_probability=0.5,
        )]
        rca._cache_put(anchor, hyps)
        got = rca._cache_get(anchor)
        assert got is not None and got[0].cause_label == "x"

    def test_cache_miss_for_different_anchor(self):
        a1 = _anchor()
        a2 = _anchor()
        from models.schemas import RcaHypothesis
        rca._cache_put(a1, [RcaHypothesis(
            cause_label="x", rationale_short="r",
            evidence_to_gather=[], prior_probability=0.5,
        )])
        assert rca._cache_get(a2) is None

    def test_cache_ttl_expires(self, monkeypatch):
        from config.settings import get_settings
        monkeypatch.setattr(get_settings(), "rca_cache_ttl_seconds", 0)
        anchor = _anchor()
        from models.schemas import RcaHypothesis
        rca._cache_put(anchor, [RcaHypothesis(
            cause_label="x", rationale_short="r",
            evidence_to_gather=[], prior_probability=0.5,
        )])
        # TTL=0 -> any fetched_at older than 'now' is expired
        import time
        time.sleep(0.01)
        assert rca._cache_get(anchor) is None
