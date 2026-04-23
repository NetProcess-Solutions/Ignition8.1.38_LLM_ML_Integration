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
