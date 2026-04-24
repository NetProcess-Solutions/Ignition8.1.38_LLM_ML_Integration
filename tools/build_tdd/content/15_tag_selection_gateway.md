# 15. Tag Selection & Gateway Integration

This is one of the chapters where v3.0 most honestly diverges from v2.0.
The v2.0 design specified a fully-discovered tag registry populated by
gateway introspection; the as-built MVP runs against a hardcoded
`KEY_TAGS` list in `ignition/scripts/config.py`, with the database-side
`tag_registry` table provisioned but unpopulated. The forward path to
the v2.0-spec end-state is documented here, with the current honest
status flagged so no future maintainer is surprised.

## 15.1 What the Gateway Side Looks Like (As-Built)

Two files in [ignition/](ignition/) compose the gateway-side
integration:

- [ignition/scripts/config.py](ignition/scripts/config.py) — Jython 2.7
  config module. Holds `KEY_TAGS` (the hardcoded ~50-tag catalog),
  `AI_SERVICE_URL`, `API_KEY`, `LINE_ID`, `TAG_PROVIDER`, `COATER1_ROOT`.
- [ignition/perspective/gateway_wiring.py](ignition/perspective/gateway_wiring.py) —
  Jython 2.7 templates for the B13/A5/A6 view bindings, the
  alarm-change script, and the chat panel session-init.

`ignition/scripts/client.py` is the thin HTTP client the gateway uses
to call the FastAPI service. `discovery.py`, `selector.py`, and
`context.py` are scaffolding for the eventual switchover to gateway-side
tag discovery (see §15.4).

## 15.2 The Hardcoded `KEY_TAGS` Catalog

The current source of truth for what tags exist on Coater 1 is the
`KEY_TAGS` constant in `ignition/scripts/config.py`:

```python
KEY_TAGS = [
    {"name": "IsRunning",         "category": "state",     "core": True,
     "keywords": []},
    {"name": "LineSpeed",         "category": "speed",     "core": True,
     "keywords": ["speed", "fpm", "rate"]},
    {"name": "StyleID",           "category": "style",     "core": True,
     "keywords": []},
    {"name": "FrontStep",         "category": "position",  "core": True,
     "keywords": ["step", "position"]},
    {"name": "ZoneTemp1",         "category": "temperature", "core": False,
     "keywords": ["zone 1", "z1", "temp"]},
    {"name": "ZoneTemp2",         "category": "temperature", "core": False,
     "keywords": ["zone 2", "z2", "temp"]},
    {"name": "ZoneTemp3",         "category": "temperature", "core": False,
     "keywords": ["zone 3", "z3", "temp"]},
    # ... ~45 more entries, hand-curated
]
```

Each entry carries:

- `name` — the tag name relative to `COATER1_ROOT`
- `category` — coarse grouping (`state`, `speed`, `temperature`,
  `pressure`, `tension`, `tenter`, `pump`, `alarm`, …)
- `core` — `True` for tier-1 always-include, `False` for tier-2
  query-routed
- `keywords` — phrases that should route this tag in if present in the
  query

The catalog is read by the service-side
`services.tag_selector.select_tags(query, anchor)` via a JSON dump that
the gateway POSTs to the service at startup (or on KEY_TAGS change).
The `_PERMISSIONS_CACHE` analog `_TAG_CATALOG_CACHE` holds the latest
catalog with no TTL but reload-on-version-bump.

## 15.3 What Tag Selection Does (As-Built)

[service/services/tag_selector.py](service/services/tag_selector.py)
implements two-tier selection:

1. **Tier-1 (always-include):** every catalog entry with `core = True`
   is included unconditionally.
2. **Tier-2 (query-routed):** for each non-core entry, include if:
   - any element of `keywords` appears in the lowercased query, OR
   - the entry's `category` is in `CATEGORY_SYNONYMS[anchor.failure_mode_scope[0]]`,
     OR
   - the `_ZONE_RX` regex matches the query and the entry's
     `category == "temperature"` and the entry name encodes the
     matched zone

`CATEGORY_SYNONYMS` is a dict in `tag_selector.py`:

```python
CATEGORY_SYNONYMS = {
    "delam_hotpull":     ["temperature", "tension", "speed"],
    "delam_coldpull":    ["temperature", "tension", "humidity"],
    "off_tenter":        ["tenter", "speed", "temperature", "tension"],
    "sag":               ["pump", "pressure", "viscosity"],
    "coating_weight_var":["pump", "speed", "metering"],
    "pinhole":           ["pressure", "viscosity", "filter"],
    # ... ~25 entries
}
```

`_ZONE_RX` matches `zone\s*([1-9])`, `z([1-9])`, or `zone\s*(one|two|three|four|five|six)`.
The matched group selects only the relevant zone's tags rather than
pulling all temperature tags.

The selection result is the input to `services/context_assembler.py`
which renders each selected tag with its full evidence rendering
(chapter 4 §4.5).

## 15.4 The `tag_registry` Forward Path

The service-side `tag_registry` table (chapter 5 §5.3) is provisioned
but empty in the MVP. The forward path to populate it from gateway
introspection:

1. **Gateway-side enumeration script.** Runs `system.tag.browse(path,
   recursive=True)` against `COATER1_ROOT`, walks `ItemInstance`
   results, classifies each tag by:
   - PLC datatype → `tag_class` (`scalar | bool | enum | aggregate`)
   - Engineering units (from tag metadata) → `engineering_units`
   - Category inferred from path segments and category synonyms
   - `core = True` for the small whitelisted critical-path set
   - `keywords` autogenerated from path segment + category
2. **POST to `/api/tag_registry/sync`.** A new endpoint accepts the
   enumeration result, upserts into `tag_registry`, returns a summary
   (added/updated/deprecated counts).
3. **Selector swap.** `services.tag_selector` reads from
   `tag_registry` instead of the cached `KEY_TAGS` JSON. Backward
   compatibility: if `tag_registry` is empty, fall back to
   `KEY_TAGS`. Cutover is therefore zero-downtime.
4. **Per-shift refresh.** The enumeration script is scheduled in the
   gateway to run nightly; new tags appear in the registry without
   engineer intervention; deprecated tags are flagged for review.

This is a ~3-week piece of work. It is not in the v3.0 cut because:

- The hardcoded list works for the pilot
- Swapping to discovered tags introduces a per-tag classification
  step (manual or ML-assisted) for the categories that path-segment
  inference doesn't cover
- The schema is forward-compatible — no service-side rework needed
  when the swap happens

## 15.5 Auto-Trigger Path

`ignition/perspective/gateway_wiring.py` ships templates for three
auto-trigger paths:

- **B13 (alarm-triggered chat)** — when a configured high-priority
  alarm fires, the gateway's tag-change script POSTs to
  `/api/chat` with a synthetic query like *"why is HighTempZone3
  active right now?"* and the resolved alarm context. The conversation
  ID is stamped with the alarm event id so subsequent operator
  follow-ups thread correctly.
- **A5 (event-triggered chat)** — same shape, triggered on
  `defect_event` insertions: *"what's the most likely cause of
  defect QR-NNNNN?"*
- **A6 (shift-handoff brief)** — at shift turnover, the outgoing
  shift's supervisor can request a generated handoff summary
  (downtime events + open issues + drift flags) via a Perspective
  button. POSTs `/api/chat` with a structured handoff template.

**Status**: Templates exist in `gateway_wiring.py` but the actual
gateway-side wiring (alarm pipeline subscription, project script
deployment, Perspective button binding) is documented in
[INSTALL.md](INSTALL.md) Part 5 and is operator-side configuration,
not service-side code.

## 15.6 Why The Hardcoded List Is Not A Crisis

A skeptical reader might object: "you specified a discovered registry
and shipped a hardcoded list — that's a regression." The reasons it
isn't:

1. **Hand-curated `keywords` and `core` flags are higher-quality than
   inference will be on day 1.** A categorization pass on 50 tags by a
   process engineer produces a better catalog than autogenerated
   categories from path inference. The discovered registry will need
   manual review of inferred categories anyway.
2. **The pilot scope is one line.** Gateway discovery's value scales
   with the number of lines (the marginal cost of curating a hand list
   for line N+1 is high; the marginal cost of running discovery is
   zero). At one line, the marginal value of discovery is small.
3. **The schema is forward-compatible.** Swapping is a configuration
   change at cutover, not a data migration.
4. **The selector is unchanged.** `select_tags` works against either
   data source; the only thing that changes is where the catalog is
   read from.

The honest framing: hardcoded list **for the pilot**, registry-driven
**before the second line**. v2.0 was right about the long-term shape;
the MVP cut prioritized faster pilot start-up.

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Tag Selection & Gateway</p>
<p><span class="label">Stayed:</span> Two-tier (always-include + query-routed)
selection model. CATEGORY_SYNONYMS-based routing on failure-mode scope.
Per-zone routing via _ZONE_RX. Service-side `tag_selector.py`
implementation.</p>
<p><span class="label">Changed:</span> v2.0 specified a discovered
`tag_registry`. As-built ships a hardcoded ~50-entry `KEY_TAGS` list
in `ignition/scripts/config.py` consumed by the same selector. The
`tag_registry` table is provisioned but unpopulated. Gateway-side
auto-trigger paths (alarm-triggered chat, event-triggered chat,
shift-handoff brief) ship as templates in
`ignition/perspective/gateway_wiring.py` requiring operator-side
wiring per INSTALL.md Part 5.</p>
<p><span class="label">Considering:</span> Wire `system.tag.browse`
discovery into <code>tag_registry</code> for line N+1 (~3 weeks of work).
Per-tag ML-assisted classification when the registry exceeds ~500
entries. Auto-pruning of `KEY_TAGS` entries that haven't been tier-2
selected in 60 days. Per-tag baseline auto-fitting (currently per-shift,
could be per-tag-class).</p>
</div>
