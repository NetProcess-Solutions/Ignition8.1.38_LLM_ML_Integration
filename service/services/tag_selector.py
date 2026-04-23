"""
Pre-screen tag selector.

Goal: avoid sending all 80+ tags + their historian aggregates on every chat
query. The selector takes (a) a tag catalog with categories/keywords/core
flags and (b) the user's question, and returns the minimum subset of tags
whose live values + history should be included in the curated context.

Design constraints:
- Deterministic. No LLM call. Sub-millisecond. Auditable.
- Always includes every tag flagged core=True.
- Matches by category synonyms first, then per-tag keywords.
- Caps total selected tags so payload size is bounded.
"""
from __future__ import annotations

import re
from typing import Iterable

# Category synonyms: words/phrases in the user query that should pull in
# every tag in that category. Order doesn't matter; matching is case-insensitive
# whole-word/substring.
CATEGORY_SYNONYMS: dict[str, list[str]] = {
    "line_state":     ["running", "stopped", "down", "idle", "state", "status"],
    "speed":          ["speed", "fpm", "rate", "slow", "fast", "throughput"],
    "coating_weight": ["coating weight", "weight", "oz", "ozpersy", "oz/yd",
                       "add-on", "addon", "coverage", "cup weight", "cupweight"],
    "puddle":         ["puddle", "pan level", "pan", "level"],
    "pump":           ["pump", "air flow", "flowrate", "backpressure", "pressure"],
    "applicator":     ["applicator", "gap"],
    "width":          ["width", "trim", "tenter", "carpet width"],
    "oven_zone":      ["oven", "zone", "burner", "temperature", "temp",
                       "profile", "setpoint", "dryer"],
    "oven_exit":      ["exit temp", "exit temperature", "oven exit"],
    "drive":          ["drive", "amps", "current", "fault", "vibration",
                       "motor", "shear"],
    "accumulator":    ["accumulator", "level"],
    "recipe":         ["style", "recipe", "product", "spec", "step"],
    "sewin":          ["sewin", "sew", "slat", "guider", "jbox"],
}

# Words like "zone 3", "zone3", "z3" → that specific zone number.
_ZONE_RX = re.compile(r"\b(?:zone\s*|z)(\d{1,2})\b", re.IGNORECASE)


def _normalize(s: str) -> str:
    return s.lower().strip()


def _matches_any(query_lower: str, terms: Iterable[str]) -> bool:
    for t in terms:
        if t in query_lower:
            return True
    return False


def select_tags(
    query: str,
    catalog: list[dict],
    max_extra: int = 20,
) -> dict:
    """
    Args:
        query: user question text
        catalog: list of tag dicts, each with at least
            { name, category, keywords, core }
        max_extra: cap on number of non-core tags to include

    Returns:
        {
            "selected_names": [str, ...],   # subset of catalog tag names
            "matched_categories": [str, ...],
            "matched_zones": [int, ...],
            "reason": "..."                  # short audit string
        }
    """
    q = _normalize(query)
    selected: list[str] = []
    seen: set[str] = set()

    # 1. Always include core tags.
    for t in catalog:
        if t.get("core"):
            if t["name"] not in seen:
                selected.append(t["name"])
                seen.add(t["name"])

    # 2. Determine which categories the query touches.
    matched_categories: list[str] = []
    for cat, syns in CATEGORY_SYNONYMS.items():
        if _matches_any(q, syns):
            matched_categories.append(cat)

    # 3. Determine specific zone numbers mentioned (e.g. "zone 3").
    matched_zones: list[int] = []
    for m in _ZONE_RX.finditer(query):
        try:
            n = int(m.group(1))
            if 1 <= n <= 99 and n not in matched_zones:
                matched_zones.append(n)
        except ValueError:
            pass

    # 4. If a specific zone was mentioned, only pull THAT zone's tags
    #    (not the entire oven_zone category). This is the common case
    #    we explicitly want to short-circuit.
    extras: list[str] = []
    if matched_zones:
        zone_name_prefixes = ["Zone" + str(n) for n in matched_zones]
        for t in catalog:
            if t["name"] in seen:
                continue
            if t["category"] != "oven_zone":
                continue
            if any(t["name"].startswith(p) for p in zone_name_prefixes):
                extras.append(t["name"])
        # Don't let the broader oven_zone category re-pull all 45 zone tags
        # just because the question also said the word "oven".
        matched_categories = [c for c in matched_categories if c != "oven_zone"]

    # 5. Pull every tag in any matched category.
    for t in catalog:
        if t["name"] in seen or t["name"] in extras:
            continue
        if t["category"] in matched_categories:
            extras.append(t["name"])

    # 6. Per-tag keyword fallback (catches things like a tag named in the
    #    question but not covered by any category synonym).
    for t in catalog:
        if t["name"] in seen or t["name"] in extras:
            continue
        kws = t.get("keywords") or []
        if _matches_any(q, [_normalize(k) for k in kws]):
            extras.append(t["name"])

    # 7. Cap.
    if len(extras) > max_extra:
        extras = extras[:max_extra]

    selected.extend(extras)

    reason_bits = []
    if matched_categories:
        reason_bits.append("categories=" + ",".join(matched_categories))
    if matched_zones:
        reason_bits.append("zones=" + ",".join(str(z) for z in matched_zones))
    if extras and not (matched_categories or matched_zones):
        reason_bits.append("keyword_match")
    if not extras:
        reason_bits.append("core_only")

    return {
        "selected_names":     selected,
        "matched_categories": matched_categories,
        "matched_zones":      matched_zones,
        "reason":             "; ".join(reason_bits),
    }
