# 7. Tool Layer & RCA Reasoning Chain (NEW vs v2.0)

This is the chapter where the v3.0 system most clearly transcends
"glorified search bar." The deterministic tool layer gives the LLM
deterministic functions it can invoke mid-completion to ground
specific numerical hypotheses; the two-step RCA reasoning chain
structures the model's causal reasoning into a hypothesize-and-adjudicate
flow with bounded budgets. Together they convert single-shot LLM
guessing into evidence-anchored ranked analysis.

The tool registry lives in
[service/services/tools.py](service/services/tools.py); the
provider-agnostic loop in
[service/services/llm.py](service/services/llm.py) (`_run_tool_loop`);
the RCA chain in [service/services/rca.py](service/services/rca.py)
with prompts in
[service/config/prompts/rca_step1_v1.txt](service/config/prompts/rca_step1_v1.txt)
and `rca_step2_v1.txt`.

## 7.1 Why Tools Exist

The LLM is a pattern-completer. It is not a calculator, not a database
client, and not a SQL planner. Asked "what percentile is this temp
reading?" it will fabricate a number that sounds right. The tool layer
intercepts that class of question and answers it deterministically:

- The percentile of `(ZoneTemp3, 435 °F)` for style S-4471 at
  front_step=2 is **89.4** by direct query against
  `feature_snapshots`. The LLM never computes it.
- The K nearest historical runs to today's run within
  `(line_speed, dwell_time, ambient_humidity)` are
  fetched by deterministic SQL.
- Drift on a tag over a 90-day window is computed via the Page-Hinkley
  CUSUM test in `services/percentiles.py`.

The LLM consumes the **results** as evidence and is required to cite
them. It can reason about the numbers but cannot make them up.

## 7.2 The Five Tools

Each tool is a typed `ToolSpec` in `services/tools.py` with a
JSON-schema parameter spec, an executor function, and a
`citation_type` mapping that auto-generates a `SourceCitation` with a
unique `id` for the LLM to cite inline.

| Tool                          | Parameters                                       | Returns                                  | Citation type     |
|-------------------------------|--------------------------------------------------|------------------------------------------|-------------------|
| `percentile_of`               | `tag, value, scope, scope_key`                   | `PercentileResult{percentile, n_samples, scope}` | `DISTRIBUTION` |
| `compare_to_distribution`     | `tag, value, scope, scope_key, k=5`              | `DistributionComparison{percentile, nearest_runs[K], outcome_breakdown}` | `DISTRIBUTION` |
| `nearest_historical_runs`     | `feature_set, target_values, k=10`               | `[NearestRun]` ranked by Mahalanobis distance | `NEAREST_RUNS` |
| `detect_drift`                | `tag, scope, scope_key, window_days=90`          | `DriftResult{status, change_point, magnitude}` | `DRIFT` |
| `defect_events_in_window`     | `start_time, end_time, line, [style], [fm_code]` | `[DefectEventSummary]`                    | `EVENT` |

Six properties hold uniformly across all five tools:

1. **Read-only.** No tool ever writes to the database.
2. **Hard SQL timeout** (`tool_sql_timeout_ms = 5000`). A pathological
   tool call cannot stall the chat turn.
3. **Bounded result size.** Every tool clamps its return cardinality
   to a small integer (`k ≤ 25`) so a tool result cannot blow the LLM
   context budget.
4. **Auto-generated citation.** Each tool result produces a
   `SourceCitation(id=..., type=..., scope=...)` that the LLM is
   instructed to cite inline as `[T1]`, `[T2]`, etc.
5. **Deterministic for a given snapshot.** Same `(tag, scope, value)`
   produces same percentile within a single chat turn (TTL cache in
   `services/percentiles.py`).
6. **Structured `ToolResult` envelope.** `ok: bool`, `data: dict`,
   `citation: SourceCitation`, `error: str|None`. The loop never
   raises into the LLM.

## 7.3 The Tool Registry as the Source of Truth

The OpenAI tool-spec emitted to the LLM is generated **from** the
registry, not written by hand:

```python
def openai_tool_specs(allowlist: set[str] | None = None) -> list[dict]:
    return [t.to_openai_spec() for t in REGISTRY if allowlist is None or t.name in allowlist]
```

This makes it structurally impossible for the LLM to call a tool that
doesn't exist. It also makes the allowlist mechanism trivially safe:
the RCA chain step 1 passes
`allowlist={"percentile_of", "compare_to_distribution", "nearest_historical_runs", "detect_drift"}`
to deliberately hide `defect_events_in_window` and any future
expensive tool from cost-budgeted hypothesis generation. Step 2 passes
the full set.

The registry is also the source of truth for citation type mapping —
the LLM never invents a citation type that doesn't correspond to a
tool. The `SourceCitation.type` field is a `Literal[...]` over the
exact set of valid types, validated by Pydantic at parse time.

## 7.4 The Tool Loop (`_run_tool_loop`)

`services/llm.py::_run_tool_loop` is the provider-agnostic engine for
tool-calling:

```
input: messages, tool_specs, max_iters, max_total_calls
budget: total_calls_remaining = max_total_calls

for iter in 0..max_iters:
    response = llm.chat_completion(messages, tools=tool_specs)
    if response.has_tool_calls:
        for call in response.tool_calls:
            if total_calls_remaining <= 0: break
            result = REGISTRY[call.name].execute(call.args)
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(result.envelope)})
            total_calls_remaining -= 1
        continue
    return response.text, accumulated_citations
```

A single `_run_tool_loop` invocation handles all three provider
implementations (`OpenAIClient`, `AzureOpenAIClient`,
`LocalOpenAICompatibleClient`) identically. The tool-call API surface
is the OpenAI-compatible `tool_calls` field; the local client adapter
maps vLLM's identical-shaped output through unchanged.

A shared `asyncio.Semaphore` (`_get_sem`) caps total concurrent LLM
calls per process. The default cap is 4
(`settings.llm_concurrency`), which keeps a single-instance service
from saturating provider rate limits.

Every tool call is persisted into `messages.tool_calls` JSONB:

```json
[
  {"call_id": "...", "tool": "percentile_of",
   "args": {"tag": "ZoneTemp3", "value": 435, "scope": "style_step",
            "scope_key": "S-4471/2"},
   "result": {"percentile": 89.4, "n_samples": 1247},
   "duration_ms": 38, "iteration": 0}
]
```

So an audit can see not just the answer but every numerical lookup the
LLM relied on to produce it.

## 7.5 The RCA Chain (B8)

For past-event queries with causal intent — operator asks *why* — the
system runs a structured two-step reasoning chain instead of a
single-shot completion. `should_use_rca_chain(query, anchor)` returns
True when:

1. `anchor.anchor_type == "past_event"`
2. The query matches a causal-intent regex (`why`, `cause`, `caused`,
   `root cause`, `root-cause`, `because`, `due to`, etc.)
3. `settings.rca_chain_enabled = True` (default)

The chain is two LLM calls separated by tool execution:

```
Step 1 — Hypothesise (rca_step1_v1.txt)
  LLM input:  curated context package, system prompt, RCA step-1 prompt
  LLM tools:  {percentile_of, compare_to_distribution,
               nearest_historical_runs, detect_drift}
  LLM output: structured list of up to 3 hypotheses, each with:
                - candidate cause statement
                - supporting evidence references (tool results + chunks)
                - confidence (low|medium|high)
                - falsifying evidence sought

Step 2 — Adjudicate (rca_step2_v1.txt)
  LLM input:  step-1 hypotheses + their tool results, full context
  LLM tools:  {all five tools}
  LLM output: ranked hypotheses with adjudication notes,
              final confidence label, narrative response with citations
```

### Bounded budget

Five caps in `settings.py` (defaults shown) keep the chain deterministic
in cost and latency:

- `rca_max_hypotheses = 3`
- `rca_max_evidence_per_hypothesis = 5`
- `rca_max_total_tool_calls = 15`
- `rca_step1_max_iters = 2`
- `rca_step2_max_iters = 2`
- `rca_step_timeout_seconds = 30`

The total tool-call budget is shared across both steps. If step 1
consumes 11 calls (high-evidence hypothesis generation), step 2 has 4
remaining and the LLM is informed of the remaining budget in its
context. A tool call that exceeds its budget returns
`ToolResult(ok=False, error="budget_exhausted")` rather than raising.

### TTL cache (`_STEP1_CACHE`)

A ~5-minute TTL in-process cache (`rca_cache_ttl_seconds = 300`)
short-circuits step 1 when the same `(anchor_event_id, anchor_run_id,
anchor_time, failure_mode, prompt_version)` key is queried twice. This
matters because operators routinely re-ask the same question after
acknowledging a clarification, and step 1 is the expensive call. Cache
hit short-circuits straight to step 2.

The cache key deliberately includes `prompt_version` so a prompt
update invalidates the cache.

### Persistence

The full RCA trace is persisted into `messages.rca_summary`:

```json
{
  "step1": {
    "model": "gpt-4o-mini-2024-07-18",
    "duration_ms": 1420,
    "tool_calls": [...],
    "hypotheses": [
      {"id": "h1", "claim": "Zone 3 element drift",
       "evidence_refs": ["t1", "t3", "doc1"], "confidence": "high"},
      {"id": "h2", ...}, {"id": "h3", ...}
    ],
    "cache_hit": false
  },
  "step2": {
    "model": "gpt-4o-mini-2024-07-18",
    "duration_ms": 980,
    "tool_calls": [...],
    "ranking": [{"id": "h1", "rank": 1, "label": "likely_contributor",
                 "adjudication": "..."}, ...],
    "final_confidence": "likely_contributor"
  },
  "total_tool_calls": 11,
  "budget_remaining": 4
}
```

Every RCA conclusion the system produces is therefore fully
reconstructible, including the tools called, the budget consumed, and
the adjudication reasoning.

## 7.6 Why Two Steps and Not One

A single-shot RCA prompt with all five tools enabled is cheaper
(one LLM call instead of two) and more flexible (the LLM picks its
own evidence-gathering depth). It is also dramatically less reliable
in this domain.

The failure mode is *premature commitment*: the LLM produces a
narrative around its first plausible hypothesis, makes a few
confirmation-bias tool calls to support it, and never seriously
considers alternatives. The two-step structure forces the model to
first commit to a list of distinct hypotheses, gather evidence for
**each**, and only then weigh them against one another. Empirically
this produces better-calibrated rankings and fewer "obvious in
hindsight" errors.

The structure also gives the audit record cleaner shape: it is
trivial to ask "did the model consider hypothesis X?" by querying
`rca_summary->step1->hypotheses`, where it is hard to extract the
same information from a free-form one-shot trace.

## 7.7 LLM Provider Abstraction (B0.5, B12)

Three providers are wired in
[service/services/llm.py](service/services/llm.py):

- **`OpenAIClient`** — public OpenAI API, `gpt-4o-mini` default model
- **`AzureOpenAIClient`** — Azure OpenAI Service, deployment-name routing
- **`LocalOpenAICompatibleClient`** — vLLM, llama.cpp server, LM Studio,
  any OpenAI-compatible endpoint. Discovery via `local_llm_endpoint`
  setting; tool-calling support assumed (vLLM ≥ 0.5)

`provider_for(name)` is the dispatcher; `llm_provider` setting picks
which one is active. Same call signature, same `_run_tool_loop`, same
behavior. The local client unblocks fully air-gapped deployments — a
common requirement for pharma, defense, and safety-critical industrial
sites that prohibit cloud LLM calls outright.

Tested in
[service/tests/test_local_llm_client.py](service/tests/test_local_llm_client.py)
(12 tests; mocks the OpenAI-shaped HTTP surface).

## 7.8 Settings Reference

| Setting                          | Default               | Effect |
|----------------------------------|-----------------------|--------|
| `llm_provider`                   | `openai`              | `openai|azure_openai|local` |
| `llm_model`                      | `gpt-4o-mini`         | Provider-specific model name |
| `llm_temperature`                | 0.1                   | Lower = more deterministic |
| `llm_max_tokens_response`        | 1500                  | Per response cap |
| `llm_concurrency`                | 4                     | In-process semaphore |
| `local_llm_endpoint`             | `""` (off)            | E.g. `http://vllm-host:8000/v1` |
| `tool_sql_timeout_ms`            | 5000                  | Per-tool hard timeout |
| `rca_chain_enabled`              | `true`                | Master toggle |
| `rca_max_hypotheses`             | 3                     | Step 1 output cap |
| `rca_max_evidence_per_hypothesis`| 5                     | Per-hypothesis evidence cap |
| `rca_max_total_tool_calls`       | 15                    | Shared step1+step2 budget |
| `rca_step1_max_iters`            | 2                     | LLM ↔ tools loop iters in step 1 |
| `rca_step2_max_iters`            | 2                     | Same in step 2 |
| `rca_step_timeout_seconds`       | 30                    | Per-step wall clock |
| `rca_cache_ttl_seconds`          | 300                   | Step-1 cache TTL |

## 7.9 Cost Profile

A typical past-event causal query with the chain enabled:

- Step 1: ~3,500 prompt tokens + ~500 completion + 4–8 tool calls
- Step 2: ~4,200 prompt tokens (carries step-1 results) + ~700 completion + 2–4 tool calls
- Total: ~9k prompt + ~1.2k completion tokens, 8–12 tool calls

At `gpt-4o-mini` rates (April 2026): **~$0.012 per RCA query**. The
shared cache, when hit, drops it to ~$0.005. A non-causal query
running through the standard one-shot path (no chain) costs ~$0.005–$0.008.

The local-provider configuration moves this to amortized hardware
cost only.

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Tools & RCA</p>
<p><span class="label">Stayed:</span> The principle that LLMs should
not do arithmetic. v2.0 noted this as a constraint; v3.0 enforces it
mechanically.</p>
<p><span class="label">Changed:</span> Whole tool layer is new.
Five deterministic tools (percentile_of, compare_to_distribution,
nearest_historical_runs, detect_drift, defect_events_in_window) with
auto-generated citations, hard timeouts, bounded result sizes,
provider-agnostic loop. Two-step hypothesise-then-adjudicate RCA chain
with bounded budget (15 total tool calls), TTL cache, full trace
persisted to <code>messages.rca_summary</code>. Local-LLM provider
(B12) shipped — fully air-gappable.</p>
<p><span class="label">Considering:</span> Self-consistency / k-sample
voting on the RCA conclusion (B6 — currently single-sample). HyDE-style
query expansion as a deterministic preliminary tool (B5). A
<code>tool: explain_anomaly</code> wrapper that consolidates the four
distributional tools into a single call when an anomaly is the entry
point. Cross-LLM ensembling (run step 2 against two providers and
diff), reserved for high-stakes safety incidents only.</p>
</div>
