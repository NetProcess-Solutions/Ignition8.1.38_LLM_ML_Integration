"""Tests for services.deviation — class-appropriate deviation tests."""
from services.deviation import (
    discrete_state_deviation,
    oscillation_deviation,
    process_following_deviation,
    setpoint_deviation,
)


def test_setpoint_deviation_in_band_returns_none():
    assert setpoint_deviation(name="Front2", current=198, setpoint=200, band=5.0) is None


def test_setpoint_deviation_out_of_band_returns_dict():
    d = setpoint_deviation(name="Front2", current=210, setpoint=200, band=5.0)
    assert d is not None
    assert d["name"] == "Front2"
    assert d["direction"] == "above"


def test_oscillation_amplitude_change_detected():
    samples = [100.0, 105.0, 100.0, 95.0, 100.0, 105.0]  # amplitude ~5
    d = oscillation_deviation(
        name="Roll1Speed",
        current=100.0,
        samples=samples,
        historical_amplitude=2.0,
        setpoint=100.0,
        amplitude_tolerance_pct=30,
    )
    assert d is not None
    assert d.get("amplitude") is not None


def test_process_following_z_score_above_threshold():
    baseline = [100.0, 101.0, 99.0, 100.0, 100.0, 101.0, 99.0, 100.0]
    d = process_following_deviation(
        name="Tension1", current=130.0,
        baseline_samples=baseline, sigma_threshold=3.0,
    )
    assert d is not None
    assert abs(d["sigma_deviation"]) >= 3.0


def test_process_following_within_baseline_no_deviation():
    baseline = [100.0, 101.0, 99.0, 100.0, 100.0]
    assert process_following_deviation(
        name="T", current=100.5, baseline_samples=baseline,
    ) is None


def test_discrete_state_unexpected_state():
    d = discrete_state_deviation(
        name="ValveState",
        current_state="closed",
        expected_states=["open"],
        transitions_in_window=0,
    )
    assert d is not None
