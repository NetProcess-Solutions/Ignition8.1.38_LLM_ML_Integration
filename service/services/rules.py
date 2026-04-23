"""
Deterministic business-rule evaluator.

Rules are stored in the `business_rules` table. A rule's `condition` is a JSON
object with this shape:

    {
        "all": [
            {"tag": "ZoneTemp3", "op": ">", "value": 425},
            {"tag": "LineSpeed", "op": ">", "value": 250}
        ]
    }

or:

    {
        "any": [
            {"tag": "Coater1Vibration", "op": ">", "value": 5.0},
            {"tag": "DriveCurrent", "op": ">", "value": 80}
        ]
    }

Combinations are not supported in MVP - keep rules simple.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from models.schemas import CuratedContextPackage


@dataclass
class MatchedRule:
    rule_id: str
    rule_name: str
    severity: str
    category: str | None
    conclusion: str
    matched_conditions: list[str]


_OPS = {
    ">":  lambda a, b: a is not None and a >  b,
    ">=": lambda a, b: a is not None and a >= b,
    "<":  lambda a, b: a is not None and a <  b,
    "<=": lambda a, b: a is not None and a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _eval_clause(clause: dict[str, Any], tag_values: dict[str, Any]) -> tuple[bool, str]:
    tag = clause.get("tag")
    op = clause.get("op")
    val = clause.get("value")
    if tag is None or op not in _OPS:
        return False, f"invalid clause {clause}"
    actual = tag_values.get(tag)
    try:
        actual_num = float(actual) if actual is not None and not isinstance(actual, bool) else actual
        val_num = float(val) if val is not None and not isinstance(val, bool) else val
    except (TypeError, ValueError):
        actual_num, val_num = actual, val
    matched = _OPS[op](actual_num, val_num)
    return matched, f"{tag}={actual} {op} {val}"


async def evaluate_rules(
    session: AsyncSession, ctx: CuratedContextPackage
) -> list[MatchedRule]:
    rows = (await session.execute(
        text(
            """
            SELECT id, rule_name, condition, conclusion, severity, category
            FROM business_rules
            WHERE line_id = :line_id AND is_active = TRUE
            """
        ),
        {"line_id": ctx.line_id},
    )).mappings().all()

    tag_values: dict[str, Any] = {t.name: t.value for t in ctx.key_tags}
    # Also expose tag summary 'current' values
    for s in ctx.tag_summaries:
        tag_values.setdefault(s.name, s.current)

    matched: list[MatchedRule] = []
    for r in rows:
        cond = r["condition"] or {}
        clauses = cond.get("all") or cond.get("any") or []
        if not clauses:
            continue
        results = [_eval_clause(c, tag_values) for c in clauses]
        is_match = (
            all(ok for ok, _ in results) if "all" in cond
            else any(ok for ok, _ in results)
        )
        if is_match:
            matched.append(MatchedRule(
                rule_id=str(r["id"]),
                rule_name=r["rule_name"],
                severity=r["severity"],
                category=r["category"],
                conclusion=r["conclusion"],
                matched_conditions=[desc for _, desc in results],
            ))
    return matched
