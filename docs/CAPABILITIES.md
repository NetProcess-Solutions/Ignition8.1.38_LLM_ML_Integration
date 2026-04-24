# Coater 1 AI Assistant — What It Means For The Line

## The pitch in one paragraph

We've built an AI assistant that lives inside Ignition Perspective and
acts like a senior process engineer who never goes home, never forgets a
past run, and reads every SOP, work order, and MOC packet on the shelf.
It watches Coater 1 in real time, spots anomalies *before* the alarm
fires, explains what's happening the moment it does, points at the most
likely root cause with cited evidence, and tells operators what worked
the last six times this exact problem showed up. It's read‑only — it
will never touch a setpoint — and every answer is auditable, scored, and
graded against what actually happened 24 hours later.

---

## Why a supervisor should care

| Pain today | What the assistant does about it |
|---|---|
| New operator hits an unfamiliar defect at 2 AM and waits until day shift to ask an engineer | Operator types the question, gets a cited answer in 3 seconds drawn from every past occurrence on the line |
| "We've seen this before" tribal knowledge walks out the door when a senior tech retires | Line memory captures those facts permanently — engineer‑reviewed, ranked first in retrieval, surfaced automatically next time the conditions match |
| Root‑cause meetings rehash the same five hypotheses with no data | Assistant pre‑computes the RCA: anchored to the event window, nearest historical runs pulled, drift + percentile math done, hypotheses ranked by evidence weight |
| Coating weight starts drifting and nobody notices for an hour | Multivariate anomaly detection on the curated tag block flags the drift in real time, *before* it crosses the alarm threshold |
| MOC review can't reconstruct what the line was actually doing during the incident | Every query persists the full plant snapshot — tags, alarms, recipe, crew, shift — replayable years later |
| Recipe creep — small setpoint tweaks accumulate into a different process | Change ledger compares the current run against the baseline recipe and flags every deviation (recipe, crew, shift, equipment, setpoints) |
| Defect repeats across shifts because the fix never made it into the SOP | Outcome closure loop follows up 24 h later — "did the fix work?" — and feeds verified outcomes back into the knowledge base |

---

## The predictive + root‑cause engine

This is the part that makes it more than a glorified search bar.

### 1. Multivariate anomaly detection (always running)

The assistant watches every tag in the Coater 1 UDT against its rolling
baseline. When two or more tags drift in a correlated way — even within
their individual alarm bands — it surfaces the pattern. Examples it will
catch *before* the operator does:

* Zone 3 temperature drifting up while line speed creeps down → coating
  weight about to go out of spec.
* Tension trending high on the unwind while web temperature drops →
  delamination risk on Style‑A in ~10 minutes.
* All three coater pump pressures slowly rising in lockstep → filter
  loading, change scheduled in next 2 hours.

### 2. Two‑step root‑cause reasoning chain

When an operator (or an alarm) asks *why*, the assistant runs a
structured reasoning chain — not a single LLM guess:

```
Step 1 — Hypothesise:    LLM proposes the top 3 candidate causes
                         from the curated context.
                              │
                              ▼
       Tools fire:      • nearest_historical_runs(event_window)
                         • detect_drift(tag, window)
                         • compare_to_distribution(tag, baseline)
                         • defect_events_in_window(±48 h)
                         • chunk_search(SOPs, work orders, line memory)
                              │
                              ▼
Step 2 — Adjudicate:    LLM weighs each hypothesis against the evidence
                         the tools returned, ranks them, assigns a
                         confidence label, and cites the proof.
```

The result is a ranked answer with citations the operator can click
through to verify — not a black‑box opinion.

### 3. Nearest‑historical‑run matching

Every coating event is fingerprinted (recipe + style + speed range +
ambient + key tag percentiles). When something goes wrong, the assistant
finds the *most similar past runs* and tells the operator:

* "The last 4 times this combination ran, scrap rate was 1.2% — today
  you're at 3.8%."
* "The closest matching run was 2026‑03‑14, work order 4521 — root
  cause was zone 3 heating element drift. Same symptoms today."

### 4. Drift, percentile, and distribution math — done deterministically

The LLM doesn't do arithmetic. A deterministic tool layer computes:

* `percentile_of(tag, value)` — where does the current reading sit in
  the historical distribution for this recipe?
* `detect_drift(tag, window)` — is this tag trending, and how fast?
* `compare_to_distribution(today, baseline_run)` — full statistical
  comparison of two runs.

These numbers are then *given* to the LLM as evidence, so it can reason
about them but cannot make them up.

### 5. Failure‑mode classification and trending

Every RCA gets classified into a structured failure‑mode code
(`delam_hotpull`, `sag`, `coating_weight_var`, …). Over time this gives
you:

* A Pareto of what's actually killing the line.
* Trend lines per failure mode per shift / crew / style.
* The ability to ask the assistant *"what failure modes are trending up
  this month and what's driving them?"* and get a real answer.

### 6. Auto‑triggered alarm explanations

The moment a configured alarm fires, the gateway script auto‑starts a
chat thread with the alarm context already loaded. The operator opens
the panel and the explanation is *already there* — likely cause, cited
evidence, recommended next steps from the SOP. No typing required.

### 7. Self‑grading precision dashboard

Every "likely" or "confirmed" RCA the assistant produces is followed up
24 hours later: was the cause it identified actually the cause? Results
roll up into `v_rca_precision_daily` — a public report card on the
assistant's own accuracy. If precision drops, you'll see it before the
operators stop trusting the tool.

---

## What an operator can ask it (the everyday view)

| Question type | Example | What it does under the hood |
|---|---|---|
| **Live state** | "What is zone 3 doing right now?" | Reads the curated tag block — current values, 30‑min trend, deviation vs baseline, active alarms |
| **Why is this happening?** | "Why did coating weight go out of spec on Friday morning?" | Anchors to the event, runs nearest‑historical‑run matching, executes the 2‑step RCA chain, returns ranked causes with citations |
| **Comparison** | "How does today's run compare to last Tuesday's good run?" | Pulls both tag traces, computes percentile + drift on each, summarises the differences in plain English |
| **Procedural lookup** | "What's the standard procedure when delamination shows up on Style‑A?" | Hybrid vector + BM25 search across SOPs, work orders, MOC packets, line memory — returns a synthesised answer with section‑level citations |
| **What changed?** | "What's different about this run vs the baseline recipe?" | Change ledger compares recipe, crew, shift, equipment, and every setpoint against the established baseline |
| **Predictive heads‑up** | "Anything I should watch out for in the next hour?" | Returns active anomalies, drifting tags, business‑rule warnings, and any open line‑memory entries that match current conditions |
| **Cross‑shift learning** | "Has B‑shift seen this defect before? What did they do about it?" | Searches structured event tables + line memory + correction history filtered to the matching shift / crew |
| **Alarm explanation** *(auto)* | Operator opens the panel after `HighTempZone3` fires — the explanation is already waiting | Gateway tag‑change script auto‑starts the thread with full alarm context the moment the alarm trips |

---

## What it knows about

* **Live plant state** — every tag in the Coater 1 UDT, deviations vs
  baseline, active alarms, current recipe / crew / shift, last setpoint
  changes.
* **Historical events** — every downtime, defect, scrap, and quality
  event from the structured event tables, all indexed and searchable.
* **Documents** — SOPs, work orders, MOC packets, equipment manuals —
  chunked structure‑aware (preserves headings, tables, lists) and
  indexed both vector + BM25 for the best of semantic and keyword
  search.
* **Line memory** — engineer‑curated tribal knowledge ("zone 3 heating
  element drifts for 48 h after replacement"). Approved memories rank
  highest in retrieval.
* **Business rules** — declarative YAML rules that fire on the live
  curated tag block (e.g. *"if line speed > 250 fpm and Style ∈ {A, B},
  surface delamination warning"*). Engineers add new rules in minutes
  without code changes.
* **Failure‑mode taxonomy** — pre‑seeded coating‑specific codes that
  every RCA classifies into, enabling Pareto and trend analysis.
* **Outcome history** — what was predicted vs what actually happened,
  feeding back into both the precision dashboard and the assistant's
  ranking model.

---

## How it stays honest (the trust model)

| Mechanism | Effect |
|---|---|
| **Curated context only** | Raw historian dumps never reach the LLM. Ignition pre‑aggregates into a strict schema — anything off‑schema is rejected before it ever touches the model. |
| **Mandatory numbered citations** | The prompt requires every claim to cite `[N]`. The response parser strips uncited claims and downgrades confidence if no citations were used. |
| **Insufficient‑evidence short‑circuit** | If retrieval comes back empty, *no LLM call happens*. The assistant returns "I don't have enough evidence to answer" instead of guessing. |
| **Confidence labels on every answer** | Every response ends with `CONFIRMED \| LIKELY \| HYPOTHESIS \| INSUFFICIENT_EVIDENCE` and the UI colour‑codes accordingly so operators know how much weight to give it. |
| **Full audit trail** | Every query persists the exact context snapshot, the tools called, the citations used, and the final response — replayable forever. Built for MOC, quality investigations, and compliance. |
| **Human‑in‑the‑loop** | New "memory" candidates and rule changes require engineer review before they affect future answers. |
| **Bounded re‑ranking from feedback** | Operator thumbs‑up/down adjusts chunk ranking by at most ±30%, so a single bad rating can't bury a useful chunk forever. |
| **Memory challenge** | Three independent challenges to a stored line memory automatically flips it to `challenged`, removing it from retrieval until an engineer reviews it. |
| **Outcome closure** | Every "likely" or "confirmed" RCA is graded 24 h later. The assistant keeps its own report card and you can see it. |
| **Read‑only by design** | No write path to PLCs, setpoints, recipes, or alarms exists in the code. Not a configuration toggle — architecturally absent. |

---

## What it deliberately won't do

* Write to PLCs, change setpoints, or close alarms. *Ever.*
* Answer questions outside the coating line's scope — it will refuse.
* Invent citations or speculate beyond the evidence in front of it.
* Operate without an audit trail.
* Replace your engineers — it makes them faster, it doesn't make
  decisions for them.

---

## Operating envelope

* **Latency:** ~2–4 seconds per query (retrieval ~150 ms, LLM 1–3 s).
* **Throughput:** ≤ 5 concurrent operators per service instance;
  horizontally scalable behind a load balancer (stateless service,
  Postgres is the only shared state).
* **LLM cost:** ~$0.005–$0.02 per query on `gpt-4o-mini`. Effectively
  free if you self‑host on a vLLM box, which is supported out of the
  box.
* **Storage growth:** ~5 MB per 1 000 queries (full audit + context
  snapshots).
* **LLM provider flexibility:** OpenAI (default), Azure OpenAI, or
  self‑hosted vLLM serving any HuggingFace causal model — pluggable, no
  code changes to switch.
* **Deployment footprint:** single Docker Compose stack — Postgres +
  pgvector + FastAPI service. Runs on a single mid‑range Linux VM.

---

## Where this is heading

The architecture is built so that today's read‑only advisor becomes
tomorrow's predictive‑maintenance and recipe‑optimisation platform
without re‑plumbing:

* The `feature_snapshots` table is already capturing the time‑series
  features needed to train predictive models on coating weight, scrap
  risk, and equipment failure.
* The `ml_models` / `ml_predictions` tables are wired in — when you're
  ready to deploy a "scrap risk in next 30 min" model, it plugs into the
  same context pipeline operators are already using.
* Every operator correction and outcome is being collected in a
  structured way, so when you decide to fine‑tune a coating‑specific
  language model, the training data is already there.

In other words: shipping this gets you the assistant *and* lays the
foundation for the predictive ML program — without a second project.

---

## Current build status

148 tests passing, 0 failing. Core capabilities — including multivariate
anomaly detection, the 2‑step RCA chain, change ledger, outcome closure,
hybrid retrieval, and the local‑LLM provider — are shipped and covered
by tests. Quality‑polish items (cross‑encoder reranker, HyDE,
self‑consistency voting, formal evaluation harness) are scaffolded with
full implementation notes and built only when measured traffic justifies
the engineering effort.

See [GAP_ANALYSIS.md](GAP_ANALYSIS.md) for the per‑item status grid and
every placeholder that must be filled in before go‑live. See
[HANDOFF_PLAYBOOK.md](HANDOFF_PLAYBOOK.md) for the step‑by‑step
finish‑up guide.
