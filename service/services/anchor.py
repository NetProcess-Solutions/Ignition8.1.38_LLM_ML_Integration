"""
Query anchor resolver (design §3.1–3.2).

Rule-based parse of an incoming query into a structured QueryAnchor that
classifies the query as past_event / current_state / pattern and pins the
anchor_time / anchor_event_id / anchor_run_id / style_scope /
failure_mode_scope / equipment_scope. Drives anchor-conditional bucket
assembly downstream.

No ML classifier in MVP. Fully deterministic, sub-millisecond, auditable.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from typing import Iterable

from models.schemas import (
    AnchorStatus,
    AnchorType,
    ClarificationOption,
    QueryAnchor,
)


# --- linguistic signals (§3.1) ----------------------------------------------

_PRESENT_TENSE_RE = re.compile(
    r"\b(rn|right now|currently|at the moment|now|today)\b|"
    r"\b(?:is|are|am)\s+(?:the|my|our|it|we|they|this|that)\b",
    re.IGNORECASE,
)
_PATTERN_RE = re.compile(
    r"\b(always|usually|often|every time|do we ever|correlate(?:s|d)?\s+with|"
    r"trend|tendency|on average|typically|whenever|each time)\b",
    re.IGNORECASE,
)

# Run-number patterns:  R-YYYYMMDD-NN, RUN-YYYYMMDD-NN, etc.
_RUN_RE = re.compile(r"\bR(?:UN)?-\d{6,8}-\d{1,3}\b", re.IGNORECASE)
# Sample / quality result pattern: QR-NNNNN
_QR_RE = re.compile(r"\bQR-\d{4,6}\b", re.IGNORECASE)

# Numeric date forms: 3/13, 3/13/2026, 03-13, 2026-03-13
_DATE_NUMERIC_RE = re.compile(
    r"\b(?:(?P<y4>20\d{2})[-/](?P<m1>\d{1,2})[-/](?P<d1>\d{1,2})"
    r"|(?P<m2>\d{1,2})[/-](?P<d2>\d{1,2})(?:[/-](?P<y2>\d{2,4}))?)\b"
)

_RELATIVE_DATE_RE = re.compile(
    r"\b(yesterday|last\s+(?:night|shift|week|run)|this\s+morning|earlier today)\b",
    re.IGNORECASE,
)

# Style codes (Shaw uses S-NNNN; broaden a touch).
_STYLE_RE = re.compile(r"\bS-\d{3,5}\b")

# Failure-mode keyword → standardized failure_mode code mapping.
# Mirrors seed_reference_data.sql. Keys are case-insensitive.
FAILURE_MODE_KEYWORDS: dict[str, str] = {
    "delam": "delam_hotpull",
    "delamination": "delam_hotpull",
    "hot pull": "delam_hotpull",
    "hotpull": "delam_hotpull",
    "cold delam": "delam_cold",
    "off tenter": "off_tenter_edge_fold",
    "off-tenter": "off_tenter_edge_fold",
    "edge fold": "off_tenter_edge_fold",
    "bubble": "bubble_adhesive",
    "bubbling": "bubble_adhesive",
    "precoat bubble": "bubble_precoat",
    "streak": "streak_frontback",
    "front back": "streak_frontback",
    "front-back": "streak_frontback",
    "coating weight": "cw_out_of_spec",
    "cw out of spec": "cw_out_of_spec",
    "ozsy": "cw_out_of_spec",
}

# Equipment scope keywords (kept small and obvious; expand from real queries).
EQUIPMENT_KEYWORDS: dict[str, str] = {
    "tillitson": "tillitson",
    "applicator": "direct_applicator",
    "directapplicator": "direct_applicator",
    "direct applicator": "direct_applicator",
    "precoat": "direct_applicator",
    "tenter": "tenter",
    "pretenter": "pretenter",
    "selvedge": "pretenter",
    "oven": "oven",
    "zone": "oven",
}

# Phrases that should trigger a control-command refusal *upstream of anchor*.
# (We don't refuse here — we just expose them so callers can route.)
CONTROL_VERBS_RE = re.compile(
    r"\b(?:set|increase|decrease|raise|lower|change|adjust|turn\s+(?:on|off)|"
    r"open|close|reset|acknowledge|ack|silence|bypass|stop|shut\s*down|"
    r"shutdown|halt|pause|resume|enable|disable)\s+(?:the\s+)?",
    re.IGNORECASE,
)


def _lower(s: str) -> str:
    return s.lower().strip()


def _first_match(rx: re.Pattern[str], text: str) -> str | None:
    m = rx.search(text)
    return m.group(0) if m else None


def _parse_date_to_datetime(
    text: str, *, today: datetime | None = None
) -> datetime | None:
    """Parse the first explicit date in `text` into a datetime (UTC, midnight)."""
    today = today or datetime.now(timezone.utc)
    rel = _RELATIVE_DATE_RE.search(text)
    if rel:
        token = rel.group(1).lower()
        if "yesterday" in token or "last night" in token:
            base = today - timedelta(days=1)
        elif "last week" in token:
            base = today - timedelta(days=7)
        elif "this morning" in token or "earlier today" in token:
            base = today
        elif "last shift" in token or "last run" in token:
            # Best-effort: treat as last 12h. Caller can refine.
            return today - timedelta(hours=12)
        else:
            base = today
        return datetime.combine(base.date(), time(0, 0), tzinfo=timezone.utc)

    m = _DATE_NUMERIC_RE.search(text)
    if not m:
        return None
    if m.group("y4"):
        y, mo, d = int(m.group("y4")), int(m.group("m1")), int(m.group("d1"))
    else:
        mo, d = int(m.group("m2")), int(m.group("d2"))
        y2 = m.group("y2")
        if y2 is None:
            y = today.year
        else:
            y = int(y2)
            if y < 100:
                y += 2000
    try:
        return datetime(y, mo, d, tzinfo=timezone.utc)
    except ValueError:
        return None


def _detect_failure_mode(text_lower: str) -> str | None:
    # Sort longer phrases first so "hot pull" beats "hot".
    for phrase in sorted(FAILURE_MODE_KEYWORDS.keys(), key=len, reverse=True):
        if phrase in text_lower:
            return FAILURE_MODE_KEYWORDS[phrase]
    return None


def _detect_equipment(text_lower: str) -> list[str]:
    found: list[str] = []
    for phrase, code in EQUIPMENT_KEYWORDS.items():
        if phrase in text_lower and code not in found:
            found.append(code)
    return found


def is_control_command(query: str) -> bool:
    """True if the query asks the assistant to perform an action."""
    return bool(CONTROL_VERBS_RE.search(query))


def resolve_anchor(
    query: str,
    *,
    now: datetime | None = None,
    candidate_runs: Iterable[dict] | None = None,
    candidate_events: Iterable[dict] | None = None,
) -> QueryAnchor:
    """
    Parse `query` into a QueryAnchor.

    `candidate_runs` / `candidate_events` are optional: when provided, they
    enable the enumerated-clarification pattern (e.g. "which roll? I see
    two scrap events from today …"). Each candidate is a dict with at
    minimum `run_number` or `event_id` and a `time` key.
    """
    now = now or datetime.now(timezone.utc)
    q_lower = _lower(query)

    explicit_run = _first_match(_RUN_RE, query)
    explicit_qr = _first_match(_QR_RE, query)
    explicit_date = _parse_date_to_datetime(query, today=now)
    has_pattern_marker = bool(_PATTERN_RE.search(query))
    has_present_marker = bool(_PRESENT_TENSE_RE.search(query))

    style = _first_match(_STYLE_RE, query)
    failure_mode = _detect_failure_mode(q_lower)
    equipment = _detect_equipment(q_lower)

    # ---- classify -------------------------------------------------------

    anchor_type: AnchorType
    if explicit_run or explicit_qr or explicit_date:
        anchor_type = "past_event"
    elif has_pattern_marker and not has_present_marker:
        anchor_type = "pattern"
    elif has_present_marker:
        anchor_type = "current_state"
    elif has_pattern_marker:
        anchor_type = "pattern"
    else:
        # Truly ambiguous — drive a clarification.
        return _build_open_clarification(query, style, failure_mode, equipment)

    # ---- assemble -------------------------------------------------------

    anchor_time: datetime | None
    if anchor_type == "past_event":
        anchor_time = explicit_date or now
    elif anchor_type == "current_state":
        anchor_time = now
    else:
        anchor_time = None

    status: AnchorStatus = "resolved"
    clarification_prompt: str | None = None
    clar_opts: list[ClarificationOption] = []

    # Past-event without an explicit run/QR but with a date — try to
    # enumerate matching events on that day.
    if (
        anchor_type == "past_event"
        and not explicit_run
        and not explicit_qr
        and candidate_events
    ):
        same_day = [
            c for c in candidate_events
            if _same_day(c.get("time"), anchor_time)
        ]
        if len(same_day) > 1:
            status = "needs_clarification_enumerated"
            clarification_prompt = (
                f"Which event? I see {len(same_day)} on "
                f"{anchor_time.strftime('%Y-%m-%d') if anchor_time else 'that day'}."
            )
            for c in same_day[:6]:
                clar_opts.append(ClarificationOption(
                    label=c.get("label") or str(
                        c.get("event_id") or c.get("run_number") or "(unknown)"
                    ),
                    anchor_event_id=c.get("event_id"),
                    anchor_run_id=c.get("run_number"),
                    anchor_time=c.get("time"),
                ))
        elif len(same_day) == 0:
            # Scoped clarification: nothing on that day, surface adjacent.
            adj = sorted(
                candidate_events,
                key=lambda c: abs((c.get("time") or now) - (anchor_time or now)),
            )[:3]
            if adj:
                status = "needs_clarification_scoped"
                clarification_prompt = (
                    "I don't see any events on that day. Did you mean one of these?"
                )
                for c in adj:
                    clar_opts.append(ClarificationOption(
                        label=c.get("label") or str(
                            c.get("event_id") or c.get("run_number") or "(unknown)"
                        ),
                        anchor_event_id=c.get("event_id"),
                        anchor_run_id=c.get("run_number"),
                        anchor_time=c.get("time"),
                    ))

    return QueryAnchor(
        anchor_type=anchor_type,
        anchor_time=anchor_time,
        anchor_event_id=explicit_qr,
        anchor_run_id=explicit_run,
        style_scope=style,
        failure_mode_scope=failure_mode,
        equipment_scope=equipment,
        anchor_status=status,
        clarification_prompt=clarification_prompt,
        clarification_options=clar_opts,
    )


def _same_day(a: datetime | None, b: datetime | None) -> bool:
    if a is None or b is None:
        return False
    return a.date() == b.date()


def _build_open_clarification(
    query: str,
    style: str | None,
    failure_mode: str | None,
    equipment: list[str],
) -> QueryAnchor:
    return QueryAnchor(
        anchor_type="current_state",  # safe default; user will refine
        anchor_time=None,
        style_scope=style,
        failure_mode_scope=failure_mode,
        equipment_scope=equipment,
        anchor_status="needs_clarification_open",
        clarification_prompt=(
            "Are you asking about a specific past event, the current state of "
            "the line, or a recurring pattern? If a specific event, please "
            "include a date, run number (R-YYYYMMDD-NN), or sample ID (QR-NNNNN)."
        ),
        clarification_options=[],
    )
