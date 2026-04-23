# ai/selector.py
# Calls the FastAPI /api/select_tags endpoint to pre-screen which tags to
# include in the curated context. Falls back to "all core tags only" if the
# service is unreachable so chat continues to work.
# Jython 2.7 compatible.

import system
import ai.config as cfg
import ai.client as client


_log = system.util.getLogger(cfg.LOGGER_NAME + ".selector")


def _core_only_names():
    return [t["name"] for t in cfg.KEY_TAGS if t.get("core")]


def selectRelevantTagNames(query, lineId=None):
    """
    Returns a Python set of tag friendly names that should be included in the
    curated context for this query.

    On any error the result falls back to core-only.
    """
    line = lineId or cfg.LINE_ID

    # Build the catalog payload (only the fields the selector needs).
    catalog = []
    for t in cfg.KEY_TAGS:
        catalog.append({
            "name":     t["name"],
            "category": t["category"],
            "keywords": list(t.get("keywords") or []),
            "core":     bool(t.get("core")),
        })

    payload = {
        "query":     query,
        "line_id":   line,
        "catalog":   catalog,
        "max_extra": cfg.SELECTOR_MAX_TAGS,
    }
    resp = client.postJson("/api/select_tags", payload)
    if not resp.get("ok"):
        _log.warn("selector unavailable, falling back to core-only: %s"
                  % resp.get("error"))
        return set(_core_only_names())

    data = resp.get("data") or {}
    names = data.get("selected_names") or []
    if not names:
        return set(_core_only_names())
    return set(names)
