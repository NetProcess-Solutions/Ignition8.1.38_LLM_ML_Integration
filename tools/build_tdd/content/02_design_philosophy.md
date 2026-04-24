# 2. Problem Statement &amp; Design Philosophy

## 2.1 The Trust Problem

Industrial environments operate on verified, auditable information. Every
decision made on the plant floor — whether to slow a line, replace a
bearing, reject a roll — is made against a backdrop of measured data and
documented procedure. An AI advisor that fabricates tag values, invents
maintenance records, or confidently asserts unsupported conclusions is not
merely unhelpful. It is an operational liability. An operator who catches
the advisor in a single confident fabrication will stop trusting it for
every subsequent interaction, and that loss is both immediate and durable.

The engineering implication is stark: the cost of a hallucinated response
is asymmetric and irrecoverable. A useful but imperfect response can be
improved through iteration. A fabricated response, even once, poisons the
operator's mental model of what the system is and what it can be trusted
to do. Anti-hallucination is therefore not a quality-of-life enhancement
to be refined over time. It is the load-bearing structural element of the
MVP, and every subsequent design decision is made in service of it.

This was the load-bearing claim in v2.0. It remains the load-bearing claim
in v3.0. Nothing in the as-built implementation softened it; if anything,
the addition of the deterministic tool layer (§7) and distributional
grounding (§8) tightened the screw — the LLM is now constrained not just
by what evidence retrieval pulls, but also by what numerical facts
deterministic tools will and will not return when it asks for them.

## 2.2 The Grounding-First Doctrine

The advisor does not write answers from its parametric knowledge. It
assembles structured evidence from the plant's systems of record, constrains
the LLM to reason only over that evidence, and enforces citation of every
factual claim. The LLM is treated as a natural-language reasoning engine
operating on a curated evidence package — not as a knowledge source in its
own right. When the evidence package is insufficient to answer a question,
the system refuses to guess and reports exactly what it was able to retrieve.

This inverts the typical LLM chatbot architecture. In a conventional chat
system, the model's priors are the primary substrate and retrieved evidence
is a helpful augmentation. Here, the retrieved evidence is the substrate,
and the model's priors are permitted to contribute only insofar as they help
structure and communicate the evidence. The model's knowledge of latex
chemistry, process control theory, or equipment nomenclature is useful for
writing readable answers; its memory of any specific plant, coater, or
product run is not trusted and is not permitted to leak into responses
without a cited source.

### Grounding-First in the As-Built Implementation

Three concrete implementation choices enforce the doctrine:

1. **`CuratedContextPackage` is the only ingress.** The Pydantic model uses
   `extra="forbid"` so a gateway script that tries to slip raw historian
   blobs into a side field is rejected at the request boundary. Plant data
   reaches the LLM only through the contract.
   *Source: `service/models/schemas.py::CuratedContextPackage` line ~150.*

2. **Section-delimited prompt with numbered citations.** The user block is
   assembled by `services/context_assembler.py` with explicit `=== TITLE ===`
   delimiters. Excluded buckets are explicitly rendered as
   `[NOT APPLICABLE — past-event query]` rather than omitted, so the model
   cannot silently blend in current-state evidence on a retrospective
   analysis.
   *Source: `services/context_assembler.py::_na_section`.*

3. **Citation-free responses are downgraded.** `services/response_parser.py`
   parses the trailing `CONFIDENCE:` line and counts citations. If the
   response makes claims but cites nothing, the parser appends a warning
   and downgrades CONFIRMED → HYPOTHESIS automatically. The LLM cannot
   smuggle ungrounded claims past the parser by skipping citation markers.
   *Source: `services/rag.py::handle_chat` ~line 360.*

## 2.3 Non-Negotiable Design Principles

These are the principles v2.0 enumerated. The as-built implementation
honors all six.

### Read-only

The advisor never commands equipment, changes setpoints, or writes to
process control tags. It is an advisory system, full stop. Every response
that includes a recommended action is framed as a suggestion for a
qualified operator to evaluate and execute, never as an automated change.

*Enforcement:* `services/anchor.py::is_control_command` regex catches verbs
like `set`, `increase`, `lower`, `change`, `adjust`, `bypass`, `acknowledge`,
`silence`, `shutdown`. Hits short-circuit to a `CONFIDENCE:
INSUFFICIENT_EVIDENCE` refusal in `rag.py` Phase 1 before the LLM is even
called. Tested in `test_anchor.py::test_control_commands_detected`.

### Every factual claim is cited

If the assistant states that a value is X, or that a document says Y, or
that a condition was observed at time Z, that claim carries a numbered
citation pointing to the exact source in the evidence package. Claims
without citations are explicitly labeled as hypothesis.

*Enforcement:* the system prompt (`config/prompts/system_prompt_v2.txt`)
mandates citation. The parser (`services/response_parser.py::has_any_citations`)
flags responses with zero citation markers and downgrades the confidence
label.

### Confidence is labeled

Every conclusion in a response is tagged as confirmed fact, likely
contributor, hypothesis, or insufficient evidence. The operator is never
forced to guess at the advisor's epistemic state.

*Enforcement:* the system prompt emits a final `CONFIDENCE: <label>` line.
`parse_confidence` extracts it; the four labels round-trip through
`messages.confidence` (VARCHAR(20)) and surface back to the Perspective UI
where they are color-coded green/amber/grey/red.

### Refusal over guessing

If retrieval returns zero relevant documents, no rules fire, and no
relevant memory exists, the system replies with an explicit
insufficient-evidence message. It never papers over the gap with a
plausible-sounding but unsupported answer.

*Enforcement:* `services/context_assembler.py::is_evidence_insufficient`
inspects the assembled summary; if every bucket count is 0 and no rules
matched, `rag.py` short-circuits to a templated insufficient-evidence
response **without calling the LLM at all**. Tokens are saved; trust is
preserved; the audit log records the short-circuit reason. Tested in
`test_context_assembler.py::test_insufficient_when_all_buckets_empty`.

### Fully reconstructible

Every response is accompanied by a complete audit record containing the
exact evidence used. Any answer can be re-opened months later and its
reasoning traced end-to-end.

*Enforcement:* `messages.context_snapshot` JSONB column receives the
parsed anchor, every populated bucket, every excluded bucket with reason,
top-K chunk IDs with scores, work-order IDs, camera-clip handles, the
full RCA trace when the chain ran, the prompt version, model name, and
model parameters. `audit_log` mirrors a summary. Both tables are monthly
partitioned via `pg_partman` (migration 001) so retention is operational
rather than performance-dictated.

### Human-in-the-loop for durable knowledge

No user feedback signal, correction, or outcome observation automatically
becomes part of the system's durable knowledge base. Every memory entry
passes through a human engineering review.

*Enforcement:* `memory_candidates` is a staging table; promotion to
`line_memory` requires an explicit engineer action. Bounded (±30%) chunk
quality blending is the only feedback that affects retrieval scoring
without an engineer in the loop, and it is bounded precisely so that no
combination of feedback can flip ranking by more than 30%.

## 2.4 Clean Separation: Chat Context vs ML Features

The MVP establishes a strict architectural boundary between the evidence
assembled for the chat prompt and the feature vectors that will eventually
be assembled for ML training. Both draw from the same underlying data
sources, but they are structured differently, serve different consumers,
and must never be confused.

| Dimension          | Chat Grounding Context (RAG)                           | ML Feature Engineering                                      |
|--------------------|--------------------------------------------------------|-------------------------------------------------------------|
| Purpose            | Give the LLM evidence to answer the current question   | Build tabular training data for offline model fitting       |
| Format             | Natural-language summaries, structured tables, chunks  | Numeric arrays, categorical encodings, fixed-schema rows    |
| Scope              | Current question + relevant recent context             | All historical events with outcome labels                   |
| When built         | Real-time, per query                                   | Batch, offline                                              |
| Where used         | Prompt assembly → LLM                                  | `feature_snapshots` table → scikit-learn / XGBoost          |
| Tags included      | Key tags (10–30), summarized stats, deviations         | All potentially predictive tags (50–200), raw aggregates    |
| Text included      | Retrieved document chunks (readable)                   | Text-derived features (keyword counts, topic codes)         |
| Key constraint     | Readable and citable by LLM and operator               | Numeric / categorical, suitable for model input             |

The ML feature engineering pipeline (Phase 4) will draw from the same event
tables and historian data, but its outputs go into `feature_snapshots`, not
into the chat prompt. When ML models are eventually active, their
predictions — together with human-readable explanations — are added to the
chat context as one more labeled evidence section. The feature vectors
themselves are never shown raw to the LLM or to the operator.

The `feature_snapshots`, `feature_definitions`, `ml_models`, and
`ml_predictions` tables are scaffolded **today** so that when Phase 4
arrives there is already structured, timestamped history to train against.
This was the v2.0 promise; it is honored in `scripts/setup_database.sql`.
The multivariate anomaly detector (§8) is the first consumer of
`feature_snapshots` and proves the schema works under load.

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Design philosophy</p>
<p><span class="label">Stayed:</span> Every doctrine and principle from v2.0
survives unchanged. The grounding-first inversion (LLM as reasoner over a
curated package, not a knowledge source) is the load-bearing claim and
every shipped mechanism reinforces it.</p>
<p><span class="label">Changed:</span> Nothing weakened. Two mechanisms
strengthened the doctrine in implementation: (1) <code>extra="forbid"</code>
on <code>CuratedContextPackage</code> rejects gateway-side ingress
attempts; (2) parser auto-downgrade-on-no-citation closes a hole the v2.0
prose left open.</p>
<p><span class="label">Considering:</span> Per-claim citation enforcement
(today: per-response). A future revision could parse out individual claims
and require each to carry a citation; this is bounded by parser ambiguity
on multi-clause sentences.</p>
</div>
