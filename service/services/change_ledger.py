"""
Sprint 5 / B9 — Change-ledger service.

For past-event RCA queries, automatically computes "what changed?" deltas
that the LLM should treat as leading hypothesis sources:

  * Tag-level deltas vs. last-N-runs baseline (sigma-ranked).
  * Recipe / setpoint deltas vs. matched-history mean.
  * Crew / shift delta — "this is the only one of the last 12 hot pulls
    on B-shift".
  * Equipment changeover delta — assets with WO between this run and the
    prior matched-history run.

Pure read against existing tables; no new schema. Output rendered as a
`CHANGE LEDGER` section by `context_assembler` (B9.5).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.baseline_cache import (
    TagBaseline,
    get_failure_mode_matched_runs,
    get_last_n_runs,
)


@dataclass
class TagDelta:
    tag_name: str
    current_value: float
    baseline_mean: float
    baseline_std: float
    sigma: float           # (current - mean) / std
    direction: str         # "above" / "below" / "near"

    def as_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag_name,
            "current": self.current_value,
            "baseline_mean": self.baseline_mean,
            "baseline_std": self.baseline_std,
            "sigma": self.sigma,
            "direction": self.direction,
        }


@dataclass
class RecipeDelta:
    field: str
    current: Any
    baseline: Any
    note: str

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__


@dataclass
class CrewDelta:
    crew: str | None
    shift: str | None
    crew_share_in_history: float | None
    note: str

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__


@dataclass
class EquipmentChangeover:
    equipment_id: str
    wo_number: str | None
    wo_date: datetime | None
    summary: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "equipment_id": self.equipment_id,
            "wo_number": self.wo_number,
            "wo_date": self.wo_date.isoformat() if self.wo_date else None,
            "summary": self.summary,
        }


@dataclass
class ChangeLedger:
    tag_deltas: list[TagDelta] = field(default_factory=list)
    recipe_deltas: list[RecipeDelta] = field(default_factory=list)
    crew_delta: CrewDelta | None = None
    equipment_changeovers: list[EquipmentChangeover] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return (
            not self.tag_deltas
            and not self.recipe_deltas
            and self.crew_delta is None
            and not self.equipment_changeovers
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "tag_deltas": [d.as_dict() for d in self.tag_deltas],
            "recipe_deltas": [d.as_dict() for d in self.recipe_deltas],
            "crew_delta": self.crew_delta.as_dict() if self.crew_delta else None,
            "equipment_changeovers": [c.as_dict() for c in self.equipment_changeovers],
        }


# ---------------------------------------------------------------------------
# Tag deltas — current snapshot vs last-N runs of same (style, front_step)
# ---------------------------------------------------------------------------

def _tag_delta(name: str, current: float, baseline: TagBaseline,
               near_sigma: float = 1.0) -> TagDelta | None:
    if baseline.mean is None or baseline.std is None:
        return None
    std = baseline.std if baseline.std > 1e-6 else 1e-6
    sigma = (current - baseline.mean) / std
    direction = (
        "above" if sigma > near_sigma
        else "below" if sigma < -near_sigma
        else "near"
    )
    if direction == "near":
        return None
    return TagDelta(
        tag_name=name,
        current_value=current,
        baseline_mean=baseline.mean,
        baseline_std=baseline.std,
        sigma=sigma,
        direction=direction,
    )


def compute_tag_deltas(
    current_tags: dict[str, float],
    baselines: dict[str, TagBaseline],
    top_k: int = 8,
) -> list[TagDelta]:
    """Rank by absolute sigma, keep top_k. Skips tags with no baseline."""
    out: list[TagDelta] = []
    for name, val in current_tags.items():
        if val is None:
            continue
        b = baselines.get(name)
        if b is None:
            continue
        d = _tag_delta(name, float(val), b)
        if d is not None:
            out.append(d)
    out.sort(key=lambda d: abs(d.sigma), reverse=True)
    return out[:top_k]


# ---------------------------------------------------------------------------
# Recipe / setpoint deltas — current run vs. matched-history mean
# ---------------------------------------------------------------------------

async def compute_recipe_deltas(
    session: AsyncSession,
    *,
    current_recipe_id: str | None,
    current_target_specs: dict[str, Any],
    line_id: str,
    style: str,
    failure_mode: str | None,
    before: datetime | None,
) -> list[RecipeDelta]:
    out: list[RecipeDelta] = []
    if not failure_mode or not style:
        return out
    matched = await get_failure_mode_matched_runs(
        session, line_id=line_id, style=style,
        failure_mode=failure_mode, before=before, limit=20,
    )
    if not matched:
        return out

    # Recipe id delta
    recipe_ids = [m.get("recipe_id") for m in matched if m.get("recipe_id")]
    if recipe_ids and current_recipe_id and current_recipe_id not in recipe_ids:
        majority = max(set(recipe_ids), key=recipe_ids.count)
        if majority != current_recipe_id:
            out.append(RecipeDelta(
                field="recipe_id",
                current=current_recipe_id,
                baseline=majority,
                note=(
                    f"Current recipe differs from the dominant matched-history "
                    f"recipe ({majority}, {recipe_ids.count(majority)}/{len(recipe_ids)} runs)"
                ),
            ))

    # target_specs deltas (numeric only, mean across matched runs)
    spec_means: dict[str, list[float]] = {}
    for m in matched:
        ts = m.get("target_specs") or {}
        if not isinstance(ts, dict):
            continue
        for k, v in ts.items():
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            spec_means.setdefault(k, []).append(f)

    for k, current_v in current_target_specs.items():
        try:
            cur = float(current_v)
        except (TypeError, ValueError):
            continue
        prior = spec_means.get(k)
        if not prior:
            continue
        mean = sum(prior) / len(prior)
        if mean == 0:
            continue
        rel = abs(cur - mean) / abs(mean)
        if rel >= 0.05:  # >= 5% delta is interesting
            out.append(RecipeDelta(
                field=k,
                current=cur,
                baseline=round(mean, 4),
                note=f"{rel*100:.1f}% delta vs. matched-history mean",
            ))
    return out


# ---------------------------------------------------------------------------
# Crew / shift delta — this run's crew uniqueness in matched history
# ---------------------------------------------------------------------------

async def compute_crew_delta(
    session: AsyncSession,
    *,
    current_crew: str | None,
    current_shift: str | None,
    line_id: str,
    style: str,
    failure_mode: str | None,
    before: datetime | None,
) -> CrewDelta | None:
    if not (current_crew or current_shift) or not style or not failure_mode:
        return None
    matched = await get_failure_mode_matched_runs(
        session, line_id=line_id, style=style,
        failure_mode=failure_mode, before=before, limit=12,
    )
    if not matched:
        return None
    crews = [m.get("crew") for m in matched if m.get("crew")]
    if not crews or current_crew is None:
        return None
    share = crews.count(current_crew) / len(crews)
    if share <= 0.2:  # current crew rarely appears in matched history
        return CrewDelta(
            crew=current_crew,
            shift=current_shift,
            crew_share_in_history=share,
            note=(
                f"Crew '{current_crew}' appears in only {share*100:.0f}% "
                f"of the last {len(crews)} matched-history runs"
            ),
        )
    return None


# ---------------------------------------------------------------------------
# Equipment changeovers — WOs between current and prior matched run
# ---------------------------------------------------------------------------

async def compute_equipment_changeovers(
    session: AsyncSession,
    *,
    line_id: str,
    style: str,
    failure_mode: str | None,
    before: datetime | None,
    equipment_scope: list[str] | None,
) -> list[EquipmentChangeover]:
    if not failure_mode or not style:
        return []
    prior = await get_failure_mode_matched_runs(
        session, line_id=line_id, style=style,
        failure_mode=failure_mode, before=before, limit=1,
    )
    if not prior:
        return []
    prior_time = prior[0].get("detected_time") or prior[0].get("end_time")
    if not isinstance(prior_time, datetime):
        return []
    current_time = before or datetime.now(timezone.utc)

    where = ["wo.date_completed >= :since",
             "wo.date_completed <  :until",
             "wo.line_id = :line"]
    params: dict[str, Any] = {
        "since": prior_time, "until": current_time, "line": line_id,
    }
    if equipment_scope:
        where.append("LOWER(wo.equipment_id) = ANY(:eq)")
        params["eq"] = [e.lower() for e in equipment_scope]

    sql = text(f"""
        SELECT wo.equipment_id, wo.wo_number, wo.date_completed, wo.problem_description
        FROM   work_orders wo
        WHERE  {' AND '.join(where)}
        ORDER BY wo.date_completed
        LIMIT 25
    """)
    rows = (await session.execute(sql, params)).mappings().all()
    return [
        EquipmentChangeover(
            equipment_id=r["equipment_id"] or "(unspecified)",
            wo_number=r["wo_number"],
            wo_date=r["date_completed"],
            summary=r["problem_description"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------

async def build_change_ledger(
    session: AsyncSession,
    *,
    current_tags: dict[str, float],
    baselines: dict[str, TagBaseline],
    current_recipe_id: str | None,
    current_target_specs: dict[str, Any],
    current_crew: str | None,
    current_shift: str | None,
    line_id: str,
    style: str | None,
    failure_mode: str | None,
    before: datetime | None,
    equipment_scope: list[str] | None,
) -> ChangeLedger:
    tag_deltas = compute_tag_deltas(current_tags, baselines)
    recipe_deltas: list[RecipeDelta] = []
    crew_delta: CrewDelta | None = None
    changeovers: list[EquipmentChangeover] = []
    if style:
        recipe_deltas = await compute_recipe_deltas(
            session,
            current_recipe_id=current_recipe_id,
            current_target_specs=current_target_specs or {},
            line_id=line_id, style=style,
            failure_mode=failure_mode, before=before,
        )
        crew_delta = await compute_crew_delta(
            session,
            current_crew=current_crew, current_shift=current_shift,
            line_id=line_id, style=style,
            failure_mode=failure_mode, before=before,
        )
        changeovers = await compute_equipment_changeovers(
            session,
            line_id=line_id, style=style,
            failure_mode=failure_mode, before=before,
            equipment_scope=equipment_scope,
        )
    return ChangeLedger(
        tag_deltas=tag_deltas,
        recipe_deltas=recipe_deltas,
        crew_delta=crew_delta,
        equipment_changeovers=changeovers,
    )
