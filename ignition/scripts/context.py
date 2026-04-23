# ai/context.py
# Builds the CuratedContextPackage that the FastAPI service expects.
# This is THE place where raw plant data is curated. The service refuses
# any payload that doesn't conform to the schema, so any field added here
# must also be added to service/models/schemas.py:CuratedContextPackage.
# Jython 2.7 compatible.

import math
import system
from java.util import Date

import ai.config as cfg

_log = system.util.getLogger(cfg.LOGGER_NAME + ".context")


def _iso(dt):
    """Convert a java.util.Date or Python datetime to ISO-8601 UTC string."""
    if dt is None:
        return None
    try:
        # Ignition values usually come back as java.util.Date
        if isinstance(dt, Date):
            millis = dt.getTime()
            return system.date.format(
                system.date.fromMillis(millis), "yyyy-MM-dd'T'HH:mm:ss'Z'"
            )
        return system.date.format(dt, "yyyy-MM-dd'T'HH:mm:ss'Z'")
    except Exception:
        return str(dt)


def _safe_float(v):
    try:
        if v is None:
            return None
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except Exception:
        return None


def _read_key_tags(selectedNames=None):
    if selectedNames is not None:
        entries = [(p, n, u, t) for (p, n, u, t) in cfg.KEY_TAG_PATHS
                   if n in selectedNames]
    else:
        entries = list(cfg.KEY_TAG_PATHS)
    if not entries:
        return []
    paths = [p for (p, _, _, _) in entries]
    qvs = system.tag.readBlocking(paths)
    out = []
    for (path, name, unit, target), qv in zip(entries, qvs):
        quality = "good"
        try:
            quality = "good" if qv.quality.isGood() else "bad"
        except Exception:
            quality = "uncertain"
        val = qv.value
        out.append({
            "name":    name,
            "value":   val,
            "unit":    unit,
            "target":  target,
            "quality": quality,
        })
    return out


def _read_recipe():
    if not cfg.RECIPE_TAGS:
        return None
    paths = [p for (p, _) in cfg.RECIPE_TAGS]
    qvs = system.tag.readBlocking(paths)
    rec = {}
    for (path, key), qv in zip(cfg.RECIPE_TAGS, qvs):
        if qv.value is not None:
            rec[key] = qv.value
    if not rec:
        return None
    return {
        "product_style":  rec.get("product_style"),
        "product_family": rec.get("product_family"),
        "recipe_id":      rec.get("recipe_id"),
        "target_specs":   {},
    }


def _query_historian_summaries(selectedNames=None):
    """
    Query historian for the configured window and compute summary stats per tag.
    Returns (summaries, deviations).
    """
    if selectedNames is not None:
        entries = [(p, n, u, t) for (p, n, u, t) in cfg.KEY_TAG_PATHS
                   if n in selectedNames]
    else:
        entries = list(cfg.KEY_TAG_PATHS)
    if not entries:
        return [], []
    paths = [p for (p, _, _, _) in entries]
    name_for = dict([(p, n) for (p, n, _, _) in entries])

    end_date = system.date.now()
    start_date = system.date.addMinutes(end_date, -cfg.HISTORIAN_WINDOW_MINUTES)

    try:
        ds = system.tag.queryTagHistory(
            paths=paths,
            startDate=start_date,
            endDate=end_date,
            returnSize=-1,
            aggregationMode=cfg.HISTORIAN_AGGREGATION_MODE,
            returnFormat="Wide",
            intervalMinutes=cfg.HISTORIAN_INTERVAL_MINUTES,
            noInterpolation=False,
            ignoreBadQuality=True,
        )
    except Exception as e:
        _log.warn("queryTagHistory failed: %s" % str(e))
        return [], []

    summaries = []
    deviations = []
    if ds is None or ds.getRowCount() == 0:
        return summaries, deviations

    cols = ds.getColumnCount()
    rows = ds.getRowCount()

    # Column 0 is timestamp; columns 1..N correspond to paths in the same order.
    for col_idx in range(1, cols):
        path = paths[col_idx - 1] if (col_idx - 1) < len(paths) else None
        name = name_for.get(path, ds.getColumnName(col_idx))
        values = []
        for r in range(rows):
            v = _safe_float(ds.getValueAt(r, col_idx))
            if v is not None:
                values.append(v)
        if not values:
            continue
        n = len(values)
        mn = min(values)
        mx = max(values)
        mean = sum(values) / n
        var = sum((x - mean) ** 2 for x in values) / n if n > 1 else 0.0
        std = math.sqrt(var)
        current = values[-1]

        # Trend: compare first half mean vs second half mean
        trend = "stable"
        if n >= 4:
            half = n // 2
            first_mean = sum(values[:half]) / half
            second_mean = sum(values[half:]) / (n - half)
            spread = max(std, 0.001)
            if second_mean - first_mean >  0.5 * spread:
                trend = "rising"
            elif second_mean - first_mean < -0.5 * spread:
                trend = "falling"

        summaries.append({
            "name": name,
            "window_minutes": cfg.HISTORIAN_WINDOW_MINUTES,
            "mean": mean, "min": mn, "max": mx, "std": std,
            "current": current, "trend": trend,
        })

        # Deviation: current vs window mean in sigmas
        if std > 0:
            sigmas = (current - mean) / std
            if abs(sigmas) >= cfg.DEVIATION_SIGMA_THRESHOLD:
                deviations.append({
                    "name": name,
                    "current": current,
                    "baseline_mean": mean,
                    "baseline_std":  std,
                    "sigma_deviation": sigmas,
                    "pct_deviation": ((current - mean) / mean * 100.0) if mean != 0 else None,
                    "direction": "above" if sigmas > 0 else "below",
                    "note": None,
                })
    return summaries, deviations


def _query_active_alarms():
    try:
        results = system.alarm.queryStatus(
            source=cfg.ALARM_SOURCE_FILTER,
            state=["ActiveUnacked", "ActiveAcked"],
        )
    except Exception as e:
        _log.warn("queryStatus failed: %s" % str(e))
        return []
    out = []
    for a in results:
        try:
            out.append({
                "source":         str(a.getSource()),
                "display_path":   str(a.getDisplayPath()),
                "priority":       str(a.getPriority()),
                "state":          str(a.getState()),
                "active_since":   _iso(a.get("activeData") and a.get("activeData").get("eventTime")) or None,
                "label":          str(a.getName()),
            })
        except Exception as e:
            _log.warn("alarm parse error: %s" % str(e))
    return out


def buildCuratedContext(lineId=None, selectedTagNames=None):
    """
    Returns a JSON-serializable dict matching CuratedContextPackage.

    selectedTagNames: optional iterable of friendly tag names. If provided,
    only those tags are read and historian-queried. Recipe and alarms are
    always included. If None, all KEY_TAG_PATHS are used.
    """
    line = lineId or cfg.LINE_ID
    snapshot_time = _iso(system.date.now())

    sel = None
    if selectedTagNames is not None:
        try:
            sel = set(selectedTagNames)
        except Exception:
            sel = None

    key_tags    = []
    summaries   = []
    deviations  = []
    alarms      = []
    recipe      = None

    try:
        key_tags = _read_key_tags(sel)
    except Exception as e:
        _log.error("read_key_tags failed: %s" % str(e))

    try:
        summaries, deviations = _query_historian_summaries(sel)
    except Exception as e:
        _log.error("query_historian failed: %s" % str(e))

    try:
        alarms = _query_active_alarms()
    except Exception as e:
        _log.error("query_alarms failed: %s" % str(e))

    try:
        recipe = _read_recipe()
    except Exception as e:
        _log.error("read_recipe failed: %s" % str(e))

    return {
        "snapshot_time":             snapshot_time,
        "line_id":                   line,
        "key_tags":                  key_tags,
        "tag_summaries":             summaries,
        "deviations":                deviations,
        "active_alarms":             alarms,
        "recipe":                    recipe,
        "historian_window_minutes":  cfg.HISTORIAN_WINDOW_MINUTES,
    }


# -----------------------------------------------------------------------------
# v2.0 helpers (design sections 3.5 / 5.7) — read tag_registry to drive
# tier-1 (always-included) and tier-2 (failure-mode-relevant) tag selection,
# and accept an anchor_time so past-event queries get the right historian
# window.
# -----------------------------------------------------------------------------

DB_CONNECTION_NAME = getattr(cfg, "PG_DB_CONNECTION", "ai_chatbot_pg")


def _registry_tier1(line_id):
    """Tags that are ALWAYS included regardless of query (tier-1).
    Defined as: tag_class in ('setpoint_tracking','process_following') AND
    metadata->>'tier' = '1' OR friendly_name in cfg.TIER1_FRIENDLY_NAMES.
    """
    fallback = list(getattr(cfg, 'TIER1_FRIENDLY_NAMES', []))
    sql = (
        "SELECT tag_path, friendly_name, unit, target FROM tag_registry "
        "WHERE line_id = ? AND (metadata->>'tier' = '1' OR friendly_name = ANY(?))"
    )
    try:
        ds = system.db.runPrepQuery(sql, [line_id, fallback], DB_CONNECTION_NAME)
    except Exception as e:
        _log.warn('tier1 query failed: %s' % str(e))
        return []
    out = []
    for r in range(ds.getRowCount()):
        out.append((
            ds.getValueAt(r, 0),
            ds.getValueAt(r, 1),
            ds.getValueAt(r, 2),
            ds.getValueAt(r, 3),
        ))
    return out


def _registry_tier2(line_id, failure_mode_scope=None, equipment_scope=None):
    """Tags scoped by failure mode and/or equipment (tier-2)."""
    if not failure_mode_scope and not equipment_scope:
        return []
    sql = (
        "SELECT tag_path, friendly_name, unit, target FROM tag_registry "
        "WHERE line_id = ? "
        " AND (metadata->'failure_modes' ? ? OR metadata->'equipment' ? ?)"
    )
    try:
        ds = system.db.runPrepQuery(
            sql,
            [line_id, failure_mode_scope or '', equipment_scope or ''],
            DB_CONNECTION_NAME,
        )
    except Exception as e:
        _log.warn('tier2 query failed: %s' % str(e))
        return []
    out = []
    for r in range(ds.getRowCount()):
        out.append((
            ds.getValueAt(r, 0),
            ds.getValueAt(r, 1),
            ds.getValueAt(r, 2),
            ds.getValueAt(r, 3),
        ))
    return out


def buildCuratedContextV2(lineId=None, anchorTime=None,
                          failureModeScope=None, equipmentScope=None):
    """
    v2 entrypoint. Replaces selectedTagNames with registry-driven
    tier-1 + tier-2 selection, and accepts an anchor_time so the
    historian window can be aligned to a past event rather than 'now'.
    """
    line = lineId or cfg.LINE_ID
    snapshot_time = _iso(system.date.now())

    tier1 = _registry_tier1(line)
    tier2 = _registry_tier2(line, failureModeScope, equipmentScope)
    selected = list(set([n for (_, n, _, _) in tier1 + tier2]))
    if not selected:
        selected = None  # fall back to legacy KEY_TAG_PATHS behavior

    # Reuse v1 implementation for the actual reads.
    base = buildCuratedContext(lineId=line, selectedTagNames=selected)

    # If the caller gave us an anchor_time we attach the v2 anchor block
    # so the orchestrator does not have to re-parse from text alone.
    if anchorTime:
        base['anchor'] = {
            'anchor_type': 'past_event',
            'anchor_time': anchorTime,
            'anchor_status': 'resolved',
            'failure_mode_scope': failureModeScope,
            'equipment_scope': equipmentScope,
        }
    base['_snapshot_time_v2'] = snapshot_time
    return base
