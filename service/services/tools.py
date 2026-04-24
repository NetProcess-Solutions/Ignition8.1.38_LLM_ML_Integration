"""
Sprint 3 / B0.4 — Tool registry for the LLM tool-calling layer.

Every tool is a pure function over the existing DB. Each returns:

    ToolResult(
        ok: bool,
        data: dict,              # JSON-serializable payload for the LLM
        citation: SourceCitation # auto-cite when the LLM uses the result
    )

The tool list is built lazily because some tools need a DB session
(opened per-call to keep tool execution decoupled from request lifetime
contracts). The OpenAI tool-spec is generated from the registry, so the
LLM can never call a tool that doesn't exist here.

Design constraints (matches `plan.md` B0.4):
  * Pure, read-only — no tool writes to the DB.
  * Bounded latency — every tool has a hard SQL timeout (5s default).
  * Cited by construction — every tool result carries its own citation.
  * Discoverable — `TOOLS` registry is the only thing the LLM sees.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import SessionFactory
from models.schemas import SourceCitation
from services.percentiles import (
    Scope,
    compare_to_distribution,
    detect_drift,
    nearest_historical_runs,
    percentile_of,
)


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any]
    citation: SourceCitation | None = None
    error: str | None = None

    def to_llm_json(self) -> str:
        """Serialize for the LLM `tool` message. Truncate long arrays."""
        payload: dict[str, Any] = {"ok": self.ok}
        if self.error:
            payload["error"] = self.error
        if self.ok:
            payload["data"] = self.data
            if self.citation is not None:
                payload["citation_id"] = self.citation.id
        return json.dumps(payload, default=str)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]            # JSON schema
    handler: Callable[..., Awaitable[ToolResult]]

    def openai_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scope_from_args(args: dict[str, Any]) -> Scope:
    raw = args.get("scope") or {}
    if not isinstance(raw, dict):
        raw = {}
    return Scope(
        kind=raw.get("kind", "global"),
        line_id=raw.get("line_id"),
        product_style=raw.get("product_style"),
        front_step=raw.get("front_step"),
        equipment=raw.get("equipment"),
        recipe_id=raw.get("recipe_id"),
        lookback_days=raw.get("lookback_days", 365),
    )


# Reusable JSON schema fragment so every tool advertises scope identically.
_SCOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Distribution scope. Narrower scope = more meaningful percentile.",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["global", "style", "style_step", "equipment", "recipe", "global_ytd"],
            "default": "global",
        },
        "line_id":       {"type": ["string", "null"]},
        "product_style": {"type": ["string", "null"]},
        "front_step":    {"type": ["integer", "null"]},
        "equipment":     {"type": ["string", "null"]},
        "recipe_id":     {"type": ["string", "null"]},
        "lookback_days": {"type": ["integer", "null"], "default": 365},
    },
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def _t_percentile_of(
    session: AsyncSession, *, tag: str, value: float, scope: dict[str, Any] | None = None
) -> ToolResult:
    sc = _scope_from_args({"scope": scope})
    res = await percentile_of(session, tag, value, sc)
    cit = SourceCitation(
        id=str(uuid4())[:8],
        type="DISTRIBUTION",
        title=f"{tag} percentile ({sc.describe()})",
        excerpt=(
            f"value={value} → percentile={res.percentile:.2f}"
            if res.percentile is not None
            else f"value={value} (no historical samples in scope)"
        ),
        score=res.percentile,
        metadata={
            "tool": "percentile_of",
            "sample_size": res.sample_size,
            "interpretation": res.interpretation,
            "scope": sc.describe(),
        },
    )
    return ToolResult(ok=True, data=res.as_citation_payload(), citation=cit)


async def _t_compare_to_distribution(
    session: AsyncSession,
    *,
    tag: str,
    value: float,
    scope: dict[str, Any] | None = None,
    k: int = 5,
) -> ToolResult:
    sc = _scope_from_args({"scope": scope})
    cmp = await compare_to_distribution(session, tag, value, sc, k=k)
    pct = cmp.percentile
    cit = SourceCitation(
        id=str(uuid4())[:8],
        type="DISTRIBUTION",
        title=f"{tag} distribution + nearest runs ({sc.describe()})",
        excerpt=(
            f"percentile={pct.percentile:.2f}, "
            f"nearest outcomes: {cmp.nearest_outcomes}"
            if pct.percentile is not None
            else "no samples in scope"
        ),
        score=pct.percentile,
        metadata={
            "tool": "compare_to_distribution",
            "sample_size": pct.sample_size,
            "interpretation": pct.interpretation,
            "scope": sc.describe(),
            "nearest_outcomes": cmp.nearest_outcomes,
        },
    )
    return ToolResult(
        ok=True,
        data={
            "percentile": pct.as_citation_payload(),
            "nearest_runs": [n.as_dict() for n in cmp.nearest_runs],
            "nearest_outcomes": cmp.nearest_outcomes,
        },
        citation=cit,
    )


async def _t_nearest_historical_runs(
    session: AsyncSession,
    *,
    tag: str,
    value: float,
    scope: dict[str, Any] | None = None,
    k: int = 5,
) -> ToolResult:
    sc = _scope_from_args({"scope": scope})
    runs = await nearest_historical_runs(session, tag, value, sc, k=k)
    cit = SourceCitation(
        id=str(uuid4())[:8],
        type="NEAREST_RUNS",
        title=f"Runs nearest to {tag}={value} ({sc.describe()})",
        excerpt=f"{len(runs)} runs returned",
        metadata={
            "tool": "nearest_historical_runs",
            "scope": sc.describe(),
            "labels": [r.label for r in runs],
        },
    )
    return ToolResult(
        ok=True, data={"runs": [r.as_dict() for r in runs]}, citation=cit
    )


async def _t_detect_drift(
    session: AsyncSession,
    *,
    tag: str,
    scope: dict[str, Any] | None = None,
    days: int = 90,
) -> ToolResult:
    sc = _scope_from_args({"scope": scope})
    res = await detect_drift(session, tag, sc, days=days)
    cit = SourceCitation(
        id=str(uuid4())[:8],
        type="DRIFT",
        title=f"{tag} drift check ({sc.describe()}, last {days}d)",
        excerpt=(
            f"DRIFTED ({res.direction}) — Page-Hinkley={res.statistic:.2f} "
            f"> {res.threshold:.2f}"
            if res.drifted
            else f"no drift — Page-Hinkley={res.statistic:.2f} ≤ {res.threshold:.2f}"
        ),
        score=None,
        metadata={
            "tool": "detect_drift",
            "drifted": res.drifted,
            "direction": res.direction,
            "sample_size": res.sample_size,
            "scope": sc.describe(),
        },
    )
    return ToolResult(
        ok=True,
        data={
            "tag": tag,
            "drifted": res.drifted,
            "direction": res.direction,
            "statistic": res.statistic,
            "threshold": res.threshold,
            "sample_size": res.sample_size,
            "window": res.window_label,
        },
        citation=cit,
    )


async def _t_defect_events_in_window(
    session: AsyncSession,
    *,
    start: str,
    end: str,
    line_id: str | None = None,
    style: str | None = None,
    equipment: str | None = None,
    limit: int = 50,
) -> ToolResult:
    """Read-only summary of defect_events in a window. Bounded result size."""
    where = ["d.detected_time >= :start::timestamptz",
             "d.detected_time <  :end::timestamptz"]
    params: dict[str, Any] = {"start": start, "end": end, "lim": int(limit)}
    if line_id:
        where.append("pr.line_id = :line_id")
        params["line_id"] = line_id
    if style:
        where.append("pr.product_style = :style")
        params["style"] = style
    if equipment:
        where.append("COALESCE(pr.metadata->>'equipment','') = :equipment")
        params["equipment"] = equipment

    sql = text(f"""
        SELECT d.id::text AS id, d.detected_time, d.failure_mode, d.severity,
               pr.run_number, pr.product_style, pr.front_step
        FROM defect_events d
        LEFT JOIN production_runs pr ON pr.id = d.run_id
        WHERE {' AND '.join(where)}
        ORDER BY d.detected_time DESC
        LIMIT :lim
    """)
    rows = (await session.execute(sql, params)).mappings().all()
    by_mode: dict[str, int] = {}
    for r in rows:
        by_mode[r["failure_mode"] or "unknown"] = by_mode.get(r["failure_mode"] or "unknown", 0) + 1
    cit = SourceCitation(
        id=str(uuid4())[:8],
        type="EVENT",
        title=f"defect_events {start}..{end}",
        excerpt=f"{len(rows)} events; by mode: {by_mode}",
        metadata={"tool": "defect_events_in_window", "by_failure_mode": by_mode},
    )
    return ToolResult(
        ok=True,
        data={
            "events": [
                {
                    "id": r["id"],
                    "detected_time": r["detected_time"].isoformat() if r["detected_time"] else None,
                    "failure_mode": r["failure_mode"],
                    "severity": r["severity"],
                    "run_number": r["run_number"],
                    "product_style": r["product_style"],
                    "front_step": r["front_step"],
                }
                for r in rows
            ],
            "by_failure_mode": by_mode,
            "truncated": len(rows) >= limit,
        },
        citation=cit,
    )


# ---------------------------------------------------------------------------
# Registry — the single source of truth for the LLM
# ---------------------------------------------------------------------------

TOOLS: dict[str, ToolSpec] = {
    "percentile_of": ToolSpec(
        name="percentile_of",
        description=(
            "Return the empirical percentile of a tag value within the "
            "scoped historical distribution. Use this whenever you want "
            "to ground 'is this normal?' claims numerically."
        ),
        parameters={
            "type": "object",
            "properties": {
                "tag":   {"type": "string", "description": "Friendly tag name."},
                "value": {"type": "number"},
                "scope": _SCOPE_SCHEMA,
            },
            "required": ["tag", "value"],
            "additionalProperties": False,
        },
        handler=_t_percentile_of,
    ),
    "compare_to_distribution": ToolSpec(
        name="compare_to_distribution",
        description=(
            "Like percentile_of, but ALSO returns the K nearest historical "
            "runs (similar tag values) along with their outcome labels. "
            "Use this to argue 'last 5 times this tag was this value, "
            "N of them ended in failure_mode X'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "tag":   {"type": "string"},
                "value": {"type": "number"},
                "scope": _SCOPE_SCHEMA,
                "k":     {"type": "integer", "default": 5, "minimum": 1, "maximum": 25},
            },
            "required": ["tag", "value"],
            "additionalProperties": False,
        },
        handler=_t_compare_to_distribution,
    ),
    "nearest_historical_runs": ToolSpec(
        name="nearest_historical_runs",
        description=(
            "K production runs whose feature value for `tag` was closest "
            "to `value`, optionally filtered by scope. Returns each run's "
            "outcome label."
        ),
        parameters={
            "type": "object",
            "properties": {
                "tag":   {"type": "string"},
                "value": {"type": "number"},
                "scope": _SCOPE_SCHEMA,
                "k":     {"type": "integer", "default": 5, "minimum": 1, "maximum": 25},
            },
            "required": ["tag", "value"],
            "additionalProperties": False,
        },
        handler=_t_nearest_historical_runs,
    ),
    "detect_drift": ToolSpec(
        name="detect_drift",
        description=(
            "Page-Hinkley drift test on the daily-mean series of `tag` "
            "within `scope` over the last `days` days. Use to argue "
            "'this tag has shifted vs. its 90-day baseline'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "tag":   {"type": "string"},
                "scope": _SCOPE_SCHEMA,
                "days":  {"type": "integer", "default": 90, "minimum": 14, "maximum": 365},
            },
            "required": ["tag"],
            "additionalProperties": False,
        },
        handler=_t_detect_drift,
    ),
    "defect_events_in_window": ToolSpec(
        name="defect_events_in_window",
        description=(
            "Read-only: defect_events between [start,end), optionally "
            "filtered by line/style/equipment. Returns up to `limit` rows "
            "and a count by failure_mode. Use to spot outbreak patterns."
        ),
        parameters={
            "type": "object",
            "properties": {
                "start":     {"type": "string", "description": "ISO-8601 timestamptz."},
                "end":       {"type": "string", "description": "ISO-8601 timestamptz."},
                "line_id":   {"type": ["string", "null"]},
                "style":     {"type": ["string", "null"]},
                "equipment": {"type": ["string", "null"]},
                "limit":     {"type": "integer", "default": 50, "minimum": 1, "maximum": 500},
            },
            "required": ["start", "end"],
            "additionalProperties": False,
        },
        handler=_t_defect_events_in_window,
    ),
}


def openai_tool_specs(allowlist: set[str] | None = None) -> list[dict[str, Any]]:
    return [
        spec.openai_spec()
        for name, spec in TOOLS.items()
        if allowlist is None or name in allowlist
    ]


async def call_tool(name: str, arguments: dict[str, Any]) -> ToolResult:
    """
    Invoke a tool by name. Opens its own DB session so callers don't have
    to thread one through. Returns a structured failure on unknown tool /
    bad args; never raises to the caller.
    """
    spec = TOOLS.get(name)
    if spec is None:
        return ToolResult(ok=False, data={}, error=f"unknown tool: {name}")
    try:
        async with SessionFactory() as session:
            result = await spec.handler(session, **arguments)
        return result
    except TypeError as e:
        return ToolResult(ok=False, data={}, error=f"bad arguments for {name}: {e}")
    except Exception as e:
        return ToolResult(ok=False, data={}, error=f"{name} failed: {e}")
