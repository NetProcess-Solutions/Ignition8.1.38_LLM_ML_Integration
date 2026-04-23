"""
Tag-class-aware deviation tests (design §3.4).

Each tag class gets the appropriate deviation test:
- setpoint_tracking:    absolute deviation from setpoint; flag when band exceeded
- oscillating_controlled: detrended-mean shift OR amplitude/period change
- process_following:    z-score vs (style, front_step, last-4-runs) baseline
- discrete_state:       unexpected transitions / dwell-time anomalies

Each function returns either None (no deviation) or a structured dict
that the context assembler renders as a DEVIATION evidence item.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Sequence


def _clean(values: Sequence[float | int | None]) -> list[float]:
    return [float(v) for v in values if v is not None]


def setpoint_deviation(
    *,
    name: str,
    current: float,
    setpoint: float,
    band: float,
) -> dict[str, Any] | None:
    """A setpoint-tracking tag is in deviation when |current - setpoint| > band."""
    if band <= 0:
        return None
    delta = current - setpoint
    if abs(delta) <= band:
        return None
    return {
        "name": name,
        "tag_class": "setpoint_tracking",
        "current": current,
        "setpoint": setpoint,
        "band": band,
        "delta": delta,
        "direction": "above" if delta > 0 else "below",
        "note": f"setpoint band exceeded by {abs(delta) - band:.2f}",
    }


def oscillation_deviation(
    *,
    name: str,
    current: float,
    samples: Sequence[float | int | None],
    historical_amplitude: float | None = None,
    setpoint: float | None = None,
    amplitude_tolerance_pct: float = 30.0,
    mean_shift_sigma: float = 2.0,
) -> dict[str, Any] | None:
    """
    Oscillating-controlled tag: 2σ excursion is normal because std is
    inflated by the oscillation. We flag (a) detrended mean shift,
    (b) amplitude change vs historical.
    """
    vals = _clean(samples)
    if len(vals) < 5:
        return None

    mean = statistics.mean(vals)
    # Approximate amplitude as half of (max - min). A real implementation
    # would use FFT; this is the MVP heuristic per §3.4.
    amplitude = (max(vals) - min(vals)) / 2.0

    issues: list[str] = []

    if setpoint is not None:
        # Detrended-mean shift: how far the running mean has drifted from SP
        # in units of the in-window std-of-mean.
        sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        sd_of_mean = (sd / math.sqrt(len(vals))) if sd > 0 else 0.0
        if sd_of_mean > 0:
            sigma = abs(mean - setpoint) / sd_of_mean
            if sigma >= mean_shift_sigma:
                issues.append(
                    f"detrended mean {mean:.2f} drifted {sigma:.1f}σ from SP {setpoint}"
                )

    if historical_amplitude is not None and historical_amplitude > 0:
        pct = (amplitude - historical_amplitude) / historical_amplitude * 100.0
        if abs(pct) >= amplitude_tolerance_pct:
            direction = "wider" if pct > 0 else "tighter"
            issues.append(
                f"amplitude {amplitude:.2f} vs historical "
                f"{historical_amplitude:.2f} ({pct:+.0f}%, {direction})"
            )

    if not issues:
        return None
    return {
        "name": name,
        "tag_class": "oscillating_controlled",
        "current": current,
        "running_mean": mean,
        "amplitude": amplitude,
        "historical_amplitude": historical_amplitude,
        "setpoint": setpoint,
        "note": "; ".join(issues),
    }


def process_following_deviation(
    *,
    name: str,
    current: float,
    baseline_samples: Sequence[float | int | None],
    sigma_threshold: float = 3.0,
) -> dict[str, Any] | None:
    """
    Process-following tag: z-score against the (style, front_step,
    last-4-runs) baseline. Flag at >= sigma_threshold.
    """
    vals = _clean(baseline_samples)
    if len(vals) < 3:
        return None
    mu = statistics.mean(vals)
    sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    if sd <= 0:
        return None
    z = (current - mu) / sd
    if abs(z) < sigma_threshold:
        return None
    return {
        "name": name,
        "tag_class": "process_following",
        "current": current,
        "baseline_mean": mu,
        "baseline_std": sd,
        "sigma_deviation": z,
        "direction": "above" if z > 0 else "below",
        "note": f"{abs(z):.1f}σ from baseline mean over last 4 runs",
    }


def discrete_state_deviation(
    *,
    name: str,
    current_state: Any,
    expected_states: Sequence[Any],
    transitions_in_window: int = 0,
    expected_transition_rate: float | None = None,
) -> dict[str, Any] | None:
    """
    Discrete/state tag: flag if (a) current state isn't in the expected
    set, or (b) transition rate is anomalous.
    """
    issues: list[str] = []
    if expected_states and current_state not in expected_states:
        issues.append(
            f"state {current_state!r} not in expected {list(expected_states)!r}"
        )
    if (
        expected_transition_rate is not None
        and transitions_in_window > expected_transition_rate * 2
    ):
        issues.append(
            f"{transitions_in_window} transitions vs expected ~{expected_transition_rate}"
        )
    if not issues:
        return None
    return {
        "name": name,
        "tag_class": "discrete_state",
        "current_state": current_state,
        "transitions_in_window": transitions_in_window,
        "note": "; ".join(issues),
    }
