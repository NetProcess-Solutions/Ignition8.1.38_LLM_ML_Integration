"""Tests for services.anchor — query parsing and clarification logic."""
from datetime import datetime, timezone

from services.anchor import is_control_command, resolve_anchor


_NOW = datetime(2024, 6, 15, 14, 30, tzinfo=timezone.utc)


def test_explicit_run_id_resolves_to_past_event():
    a = resolve_anchor("Why did R-20240601-03 fail?", now=_NOW)
    assert a.anchor_type == "past_event"
    assert a.anchor_run_id == "R-20240601-03"
    assert a.anchor_status == "resolved"


def test_relative_yesterday_morning_resolves():
    a = resolve_anchor("What happened yesterday morning?", now=_NOW)
    assert a.anchor_type == "past_event"
    assert a.anchor_time is not None
    assert a.anchor_time.date() == datetime(2024, 6, 14).date()
    assert a.anchor_status == "resolved"


def test_present_tense_is_current_state():
    a = resolve_anchor("Is the line running normally right now?", now=_NOW)
    assert a.anchor_type == "current_state"
    assert a.anchor_status == "resolved"


def test_pattern_query_detected():
    a = resolve_anchor("How often do we see delamination on style S-1234?", now=_NOW)
    assert a.anchor_type == "pattern"
    assert a.style_scope == "S-1234"
    assert a.failure_mode_scope == "delam_hotpull"


def test_ambiguous_no_anchor_status_clarification():
    a = resolve_anchor("delam", now=_NOW)
    # bare keyword with no time/run/event -> needs clarification
    assert a.anchor_status in (
        "clarification_needed", "ambiguous",
        "needs_clarification_open", "needs_clarification_enumerated",
        "needs_clarification_scoped",
    )


def test_control_commands_are_detected():
    assert is_control_command("Set Front2 to 195")
    assert is_control_command("Stop the line")
    assert is_control_command("change the setpoint to 250")
    assert not is_control_command("What was the setpoint at noon?")
    assert not is_control_command("Why did Front2 drop?")
