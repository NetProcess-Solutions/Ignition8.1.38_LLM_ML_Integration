# ai/discovery.py
# -----------------------------------------------------------------------------
# Autonomous tag discovery (design section 3.6 / 5.7).
#
# Walks the Ignition tag tree using system.tag.browse(), discovers all
# Coater-1 OPC-UA tags and Memory tags, computes update rates and
# value-distribution heuristics, and writes them to the central
# `tag_registry` table in PostgreSQL via Ignition's named DB connection.
#
# Runs nightly. The orchestrator's tag_selector reads tag_registry to
# decide which tags belong in tier-1 (always-included) and tier-2
# (anchored / failure-mode-relevant) for any given query.
#
# Jython 2.7 compatible.
# -----------------------------------------------------------------------------

import system
import time

import ai.config as cfg

_log = system.util.getLogger(cfg.LOGGER_NAME + ".discovery")

# Roots to crawl. Each entry must be a tag-provider-qualified path.
DISCOVERY_ROOTS = [
    "[default]Coater1",
    "[default]Plant4/Coater1",
]

# Postgres connection name as configured in the Ignition Gateway under
# Config -> Databases -> Connections. Schema columns must match
# scripts/setup_database.sql:tag_registry.
DB_CONNECTION_NAME = getattr(cfg, "PG_DB_CONNECTION", "ai_chatbot_pg")

# Tag classes (mirrors service/models/schemas.py:TagClass)
CLASS_SETPOINT_TRACKING   = "setpoint_tracking"
CLASS_OSCILLATING         = "oscillating_controlled"
CLASS_PROCESS_FOLLOWING   = "process_following"
CLASS_DISCRETE_STATE      = "discrete_state"

_SETPOINT_HINTS = ("sp", "setpoint", "set_point", "target")
_DISCRETE_DATATYPES = ("Boolean", "Int1", "Int2", "Int4", "Int8")


def _is_setpoint(name_lower):
    return any(h in name_lower for h in _SETPOINT_HINTS)


def _classify(tag_path, datatype, browse_tags):
    """
    Heuristic class assignment. Engineering review can override later by
    setting `manual_override = TRUE` in tag_registry.
    """
    name_lower = tag_path.lower()
    if _is_setpoint(name_lower):
        return CLASS_SETPOINT_TRACKING
    if datatype in _DISCRETE_DATATYPES:
        return CLASS_DISCRETE_STATE
    # Heuristic: if there is a sibling tag containing 'sp' / 'setpoint',
    # this is a process-following PV. Otherwise default to oscillating.
    base = tag_path.rsplit("/", 1)[0] if "/" in tag_path else tag_path
    siblings = [t.fullPath for t in browse_tags if t.fullPath.startswith(base + "/")]
    for s in siblings:
        sl = s.lower()
        if _is_setpoint(sl):
            return CLASS_PROCESS_FOLLOWING
    return CLASS_OSCILLATING


def _walk(root):
    """
    Iteratively browse the tag tree below `root`. Returns a list of dicts:
        { fullPath, name, dataType, valueSource, hasChildren }
    """
    out = []
    stack = [root]
    seen = set()
    while stack:
        path = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        try:
            results = system.tag.browse(path, {"recursive": False})
        except Exception as e:
            _log.warn("browse failed at %s: %s" % (path, str(e)))
            continue
        for entry in results.getResults():
            full = str(entry["fullPath"])
            dtype = str(entry.get("dataType") or "")
            vsrc = str(entry.get("valueSource") or "")
            has_children = bool(entry.get("hasChildren"))
            out.append({
                "fullPath": full,
                "name": full.rsplit("/", 1)[-1],
                "dataType": dtype,
                "valueSource": vsrc,
                "hasChildren": has_children,
            })
            if has_children:
                stack.append(full)
    return out


def _sample_history_stats(tag_path, window_minutes=1440):
    """
    24h sample to estimate update rate, observed min/max, and rough
    oscillation amplitude. Skipped if historian has no data.
    """
    try:
        end = system.date.now()
        start = system.date.addMinutes(end, -window_minutes)
        ds = system.tag.queryTagHistory(
            paths=[tag_path],
            startDate=start,
            endDate=end,
            returnSize=-1,
            aggregationMode="LastValue",
            returnFormat="Wide",
            ignoreBadQuality=True,
        )
        if ds is None or ds.getRowCount() == 0:
            return None
        n = ds.getRowCount()
        vals = []
        for r in range(n):
            v = ds.getValueAt(r, 1)
            try:
                vals.append(float(v))
            except Exception:
                pass
        if not vals:
            return {"sample_count_24h": n}
        return {
            "sample_count_24h": n,
            "min_24h": min(vals),
            "max_24h": max(vals),
            "amplitude_24h": (max(vals) - min(vals)) / 2.0,
        }
    except Exception as e:
        _log.warn("history sample failed for %s: %s" % (tag_path, str(e)))
        return None


def _upsert_registry_row(row):
    """
    Upsert one tag_registry row via runNamedQuery if available, else raw SQL.
    Schema (per scripts/setup_database.sql):
      tag_path TEXT PK, line_id, friendly_name, tag_class, data_type,
      value_source, unit, target, sample_count_24h, min_observed,
      max_observed, amplitude_observed, manual_override BOOL,
      last_seen_at TIMESTAMPTZ, metadata JSONB
    """
    sql = (
        "INSERT INTO tag_registry ("
        "  tag_path, line_id, friendly_name, tag_class, data_type, "
        "  value_source, unit, sample_count_24h, min_observed, "
        "  max_observed, amplitude_observed, last_seen_at, metadata"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW(), CAST(? AS jsonb)) "
        "ON CONFLICT (tag_path) DO UPDATE SET "
        "  line_id = EXCLUDED.line_id, "
        "  friendly_name = EXCLUDED.friendly_name, "
        "  data_type = EXCLUDED.data_type, "
        "  value_source = EXCLUDED.value_source, "
        "  unit = EXCLUDED.unit, "
        "  sample_count_24h = EXCLUDED.sample_count_24h, "
        "  min_observed = EXCLUDED.min_observed, "
        "  max_observed = EXCLUDED.max_observed, "
        "  amplitude_observed = EXCLUDED.amplitude_observed, "
        "  tag_class = CASE WHEN tag_registry.manual_override THEN tag_registry.tag_class "
        "                   ELSE EXCLUDED.tag_class END, "
        "  last_seen_at = NOW(), "
        "  metadata = EXCLUDED.metadata"
    )
    args = [
        row["tag_path"], row["line_id"], row["friendly_name"], row["tag_class"],
        row["data_type"], row["value_source"], row.get("unit"),
        row.get("sample_count_24h"), row.get("min_observed"),
        row.get("max_observed"), row.get("amplitude_observed"),
        system.util.jsonEncode(row.get("metadata") or {}),
    ]
    try:
        system.db.runPrepUpdate(sql, args, DB_CONNECTION_NAME)
    except Exception as e:
        _log.error("tag_registry upsert failed for %s: %s" % (row["tag_path"], str(e)))


def runDiscovery(roots=None, sample_history=True):
    """
    Public entrypoint: call from a Gateway Timer (nightly, e.g. 03:00).
    """
    started = time.time()
    roots = roots or DISCOVERY_ROOTS
    line = cfg.LINE_ID

    all_tags = []
    for r in roots:
        try:
            all_tags.extend(_walk(r))
        except Exception as e:
            _log.error("walk root %s failed: %s" % (r, str(e)))

    # Filter out folders / UDT instances that have no value of their own.
    leaves = [t for t in all_tags if not t["hasChildren"]
              and t["valueSource"] in ("opc", "memory", "expression", "derived")]
    _log.info("discovery: %d leaves under %d roots" % (len(leaves), len(roots)))

    upserted = 0
    for t in leaves:
        cls = _classify(t["fullPath"], t["dataType"], all_tags)
        row = {
            "tag_path": t["fullPath"],
            "line_id": line,
            "friendly_name": t["name"],
            "tag_class": cls,
            "data_type": t["dataType"],
            "value_source": t["valueSource"],
            "unit": None,
            "metadata": {"discovered_via": "ai.discovery.runDiscovery"},
        }
        if sample_history:
            stats = _sample_history_stats(t["fullPath"])
            if stats:
                row["sample_count_24h"] = stats.get("sample_count_24h")
                row["min_observed"] = stats.get("min_24h")
                row["max_observed"] = stats.get("max_24h")
                row["amplitude_observed"] = stats.get("amplitude_24h")
        _upsert_registry_row(row)
        upserted += 1

    _log.info("discovery complete: %d tags upserted in %.1fs"
              % (upserted, time.time() - started))
    return upserted
