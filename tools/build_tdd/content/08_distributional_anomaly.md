# 8. Distributional Grounding, Anomaly & Change Ledger (NEW vs v2.0)

This chapter documents the three numerical-evidence services that
operate alongside text retrieval and tool calls. Together they
provide the **non-text grounding substrate** that the LLM consumes:
percentiles and drift over historical distributions
(`services/percentiles.py`), multivariate anomaly scoring on live tag
snapshots (`services/anomaly.py`), and structural diff against the
matched-history baseline (`services/change_ledger.py`).

None of the three existed in the v2.0 design. All three ship in v3.0
and are exercised by the live request path in
[service/services/rag.py](service/services/rag.py).

## 8.1 The Percentile Service (`services/percentiles.py`)

The percentile service is the substrate behind the four distributional
tools (chapter 7). It computes empirical CDFs over `feature_snapshots`
joined to `production_runs`, scoped to one of six contexts.

### Six scopes

| Scope          | Filter                                     | Use case |
|----------------|--------------------------------------------|----------|
| `global`       | line_id only                               | Coarse "is this an extreme value at all?" |
| `style`        | line_id + product_style                    | "Is this high for this style?" |
| `style_step`   | line_id + product_style + front_step       | "Is this high at this position in this style?" |
| `equipment`    | line_id + equipment_id                     | "Is this drift specific to this equipment?" |
| `recipe`       | line_id + recipe_id                        | "Is this off for this recipe?" |
| `global_ytd`   | line_id, current calendar year only        | Bounds for seasonal effects |

The scope choice matters. Front2_Temp = 198 °C may be at the 89th
percentile globally, the 51st percentile for style S-1234 at
front_step=2 in summer, and the 12th percentile for recipe R-102B in
winter. Tying the percentile to context is the whole point — and is
exactly what the tool layer's `scope` parameter forwards from the
LLM's call site.

### CDF construction

Per-scope CDFs are computed lazily on demand:

```sql
SELECT (features ->> $tag)::numeric AS value
FROM feature_snapshots fs
JOIN production_runs pr ON fs.entity_id = pr.run_id
WHERE fs.entity_type = 'run'
  AND <scope predicate>
  AND fs.snapshot_time >= now() - interval '180 days'
ORDER BY value;
```

The result is sorted in-memory and percentile-of is just a binary
search. CDFs are cached in-process keyed on
`(tag, scope, scope_key)` with a TTL
(`percentile_cache_ttl_seconds = 600`). Same query inside a single
chat turn is a hash-lookup; same query across turns within ten minutes
is also a hash-lookup. After TTL expiry the next access re-computes.

A CDF requires `n_samples >= percentile_min_samples` (default 30) to
be considered representative. Below that, `PercentileResult.scope`
includes a flag `insufficient_data: true` and the LLM is instructed
not to cite the percentile as evidence.

### Drift detection (`detect_drift`)

The drift tool runs the **Page-Hinkley CUSUM test** on a 90-day
rolling daily mean of the tag, scoped identically:

```
g_t = sum from i=1 to t of (x_i - mean_baseline - delta)
m_t = min(g_0, ..., g_t)
PH_t = g_t - m_t

if PH_t > threshold: emit DRIFT_DETECTED, change_point = argmax(PH)
```

`delta` is a tolerance for the change in mean we're not interested in
(default 0.5σ); `threshold` is the alarm threshold (default 5σ).
Both configurable per-tag via `feature_definitions.metadata.drift_*`.

Returns `DriftResult{status, change_point, magnitude_sigma}` with
status one of `DRIFT_DETECTED | NO_DRIFT | INSUFFICIENT_DATA`.

Implementation is numpy-only — no `ruptures`, no `scikit-learn`. Tested
in `test_percentiles.py::test_drift_*` (14 cases).

### Nearest-runs

`nearest_historical_runs(feature_set, target_values, k)` computes
Mahalanobis distance from `target_values` to every snapshot in
`feature_snapshots` for the same line, sorted ascending. Returns the
top-K with their full feature set, run metadata, and any associated
defect_events (so the LLM can see "the closest matching past run had a
hot-pull delam and was traced to zone 3 element drift").

The Mahalanobis distance uses a covariance matrix fitted from the
**same baseline window** as the anomaly model (§8.2). Sharing the
covariance is what lets the system give consistent answers across the
"what's anomalous now" and "what runs are most similar" framings.

## 8.2 Multivariate Anomaly Detection (`services/anomaly.py`)

For current-state queries, the system scores the live tag snapshot
against a 90-day baseline using a **Mahalanobis distance** model.
Numpy-only implementation (no scikit-learn dependency), shipped in
[service/services/anomaly.py](service/services/anomaly.py) and tested
in `test_anomaly.py` (18 cases).

### Why multivariate

A univariate alarm fires when zone 3 temp crosses 440 °F. But the
operator's interesting failure mode is "zone 3 temp drifting up while
line speed creeps down" — neither leg of which trips its individual
alarm, but together they signal coating-weight loss is imminent.
Multivariate detection catches this class of correlated drift before
any individual alarm fires.

### Model fit

Once per shift (`anomaly_fit_interval_seconds = 14400`) the system
fits a `_FittedModel`:

```
1. Pull all live snapshots from feature_snapshots with entity_type='live'
   for the past 90 days.
2. Filter to "normal" runs — exclude any snapshot within ±2 h of a
   defect_event or downtime_event.
3. Build the feature matrix X (n_samples × n_features). Sparse-tag
   handling: features missing in >30% of snapshots are dropped.
4. Compute mean μ and covariance Σ.
5. Apply ridge stabilization: Σ_reg = Σ + λ·I,  λ = 1e-3 · trace(Σ)/d
   (prevents singular Σ when feature pairs are highly correlated).
6. Cache (μ, Σ_reg^-1, feature_names) for the shift.
```

### Live scoring

`score_live_snapshot(snapshot)` does:

```
1. Project snapshot onto the fitted feature names (drop unfit tags,
   zero-fill unlisted ones).
2. Compute Mahalanobis distance: d = sqrt((x-μ)^T Σ_reg^-1 (x-μ))
3. Compare to fitted-distribution p95 threshold:
       is_anomaly = d > threshold_p95
4. Per-tag attribution: rank tags by their contribution to d
   ((x_i - μ_i) * (Σ_reg^-1 (x-μ))_i, top-K).
```

Returns `AnomalyScore{distance, threshold, is_anomaly,
top_contributing_tags: [(tag_name, contribution_pct)]}`.

The top-K contributing tags is what makes the anomaly result
**actionable** rather than just "something is weird." The LLM
receives "current state is at p98 of historical distribution; the
top contributors are ZoneTemp3 (38%), TillitsonMeterRPM (24%), and
LineSpeed (18%)" and can reason about the causal structure.

### When it runs

Anomaly scoring runs in `_phase_pre_llm` of `rag.py` for current-state
queries only — the live snapshot is not meaningful for past-event
analysis. The result, when present, is rendered into prompt section M
("Multivariate Anomaly"). Past-event prompts always render this section
as `[NOT APPLICABLE — past-event query, no live snapshot]`.

If the model has not been fit yet (cold start) or the snapshot has too
few overlapping features (`< feature_min_overlap = 8`), scoring returns
`None` and section M is omitted from the prompt entirely.

## 8.3 Change Ledger (`services/change_ledger.py`)

The change ledger is a structural diff between the **current run** and
the **dominant matched-history baseline**. Where the anomaly score
flags "things are weird now," the change ledger flags "here is
specifically what is different from the last time this combination ran
well."

Lives in
[service/services/change_ledger.py](service/services/change_ledger.py),
tested in `test_change_ledger.py` (22 cases).

### Four delta types

```python
@dataclass
class TagDelta:
    tag: str
    current_value: float
    baseline_mean: float
    baseline_std: float
    sigma_offset: float          # (current - baseline_mean) / baseline_std
    direction: str               # "up" | "down"

@dataclass
class RecipeDelta:
    field: str                   # "recipe_id" | "target_specs.X" | etc.
    current: Any
    baseline_dominant: Any
    baseline_pct: float          # what % of matched-history runs used the baseline value

@dataclass
class CrewDelta:
    current_crew: str
    current_shift: str
    baseline_dominant_crew: str
    baseline_pct: float

@dataclass
class EquipmentChangeover:
    equipment_id: str
    wo_id: str
    closed_at: datetime
    summary: str                 # WO summary + parts_used
```

A `ChangeLedger` aggregates lists of all four:

```python
@dataclass
class ChangeLedger:
    tag_deltas: list[TagDelta]                       # sigma-ranked, top 10
    recipe_deltas: list[RecipeDelta]
    crew_delta: CrewDelta | None
    equipment_changeovers: list[EquipmentChangeover] # WOs in matched-history window
    baseline_run_ids: list[str]                      # the runs that defined the baseline
```

### How the baseline is chosen

`build_change_ledger(current_run, matched_history_runs)`:

1. From the matched-history runs (chapter 4 §4.3), pick the **dominant
   recipe**: the recipe_id appearing in ≥50% of runs. Below 50%,
   `recipe_deltas` returns the most-common-recipe diff with a
   `baseline_pct` < 0.5 caveat.
2. Compute per-tag baseline means and stds across **only the dominant
   recipe runs** (so RecipeDelta is reported separately, not folded
   into TagDelta).
3. Identify the dominant crew/shift; flag CrewDelta if current
   differs.
4. Pull all closed work orders against the line's equipment in the
   matched-history time window, surface as `equipment_changeovers`.
5. Tag deltas are **sigma-ranked** — the top 10 by absolute
   `sigma_offset`. Below ±2σ a tag is not surfaced (noise floor).

### Rendering into the prompt

The ledger renders as section L of the prompt (chapter 4 §4.9):

```
=== CHANGE LEDGER ===
TAG DELTAS vs. matched-history baseline (sigma-ranked):
  ZoneTemp3 +3.4σ above baseline (current 435 vs mean 421, std 4.1)
  TillitsonMeterRPM +1.8σ above baseline (current 33 vs mean 28, std 2.7)
  LineSpeed -2.1σ below baseline (current 235 vs mean 248, std 6.2)

RECIPE DELTAS:
  recipe_id differs from dominant matched-history (current R102C, baseline R102B in 6/8 prior runs)

CREW DELTA:
  current crew=B-shift, baseline dominant=A-shift in 7/8 prior runs

EQUIPMENT CHANGEOVERS in matched-history window:
  zone3_heater (WO-88214 closed 2026-04-19): element replaced, calibration drift noted
  unwind_brake (WO-88245 closed 2026-04-21): brake pad replacement, rebalance pending
```

This section is what makes the LLM's narrative response specific.
Without the change ledger, the model says "zone 3 looks elevated";
with it, the model says "zone 3 is +3.4σ above the matched-history
baseline that was set when the same crew was running R102B; the
current crew is on R102C, and zone 3's heating element was just
replaced under WO-88214 with calibration drift noted."

### When it runs

Built in `_phase_pre_llm` for past-event anchors when matched-history
runs are available (typically when the query carries a failure-mode
scope). Returns `None` for current-state and pattern queries; the prompt
omits section L when None.

## 8.4 How the Three Services Relate

The three services share a substrate (`feature_snapshots`) but expose
different framings of it:

```
                 feature_snapshots
                 (per-run, per-event, per-live)
                          │
            ┌─────────────┼──────────────┐
            ▼             ▼              ▼
       percentiles    anomaly        change_ledger
       (cross-run     (live snapshot (current run vs
        empirical CDF) vs baseline)   matched-history)
            │             │              │
            ▼             ▼              ▼
       distributional   live drift     structural
       tools            anomaly score  diff
       (LLM tool calls) (prompt §M)    (prompt §L)
```

All three answer different versions of "is this normal":

- Percentiles: "where does this single value sit in the distribution?"
- Anomaly: "is the joint state of the live snapshot anomalous?"
- Change ledger: "what is structurally different from the last good run?"

The LLM receives all three (when applicable) and is required to
reconcile them — they cannot disagree silently because they all draw
from the same underlying snapshot table.

## 8.5 Settings Reference

| Setting                              | Default      | Effect |
|--------------------------------------|--------------|--------|
| `percentile_cache_ttl_seconds`       | 600          | Per-CDF in-process cache TTL |
| `percentile_min_samples`             | 30           | Below this, CDF marked insufficient_data |
| `drift_window_days`                  | 90           | Page-Hinkley window |
| `drift_delta_sigma`                  | 0.5          | Tolerance below which we don't care |
| `drift_threshold_sigma`              | 5.0          | PH alarm threshold |
| `anomaly_fit_interval_seconds`       | 14400        | Re-fit cadence (4 h) |
| `anomaly_baseline_window_days`       | 90           | Fit window |
| `anomaly_p95_threshold`              | auto         | From fit; configurable override |
| `anomaly_feature_min_overlap`        | 8            | Min features in live snapshot |
| `change_ledger_baseline_pct_min`     | 0.5          | Min recipe dominance for clean baseline |
| `change_ledger_sigma_threshold`      | 2.0          | Tag-delta noise floor |
| `change_ledger_max_tag_deltas`       | 10           | Top-K sigma-ranked tags surfaced |

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Distributional Grounding & Anomaly</p>
<p><span class="label">Stayed:</span> The grounding-first principle.
v2.0 specified that the LLM be given pre-digested evidence rather
than raw historian data; v3.0 expands the *kinds* of pre-digested
evidence dramatically.</p>
<p><span class="label">Changed:</span> Three new services shipped.
Percentile service with six scopes, in-process CDF cache, Page-Hinkley
drift detection, Mahalanobis-distance nearest-runs. Multivariate anomaly
detection (numpy-only Mahalanobis with ridge stabilization, p95
threshold, top-K contributing tags). Structural change ledger
(TagDelta, RecipeDelta, CrewDelta, EquipmentChangeover) rendered as
prompt section L. All three share <code>feature_snapshots</code> as
substrate so they cannot disagree silently.</p>
<p><span class="label">Considering:</span> Per-tag dynamic drift
thresholds learned from history rather than the global ±5σ default.
Switching the anomaly fit to <code>scikit-learn</code> EllipticEnvelope
with bootstrap CI on the threshold (currently fixed p95). A
"counterfactual" change-ledger framing — "what would the prompt look
like if we held recipe constant?" — surfaced on demand. Time-series
forecasting models on key tags (Phase 4 ML wiring already in place).</p>
</div>
