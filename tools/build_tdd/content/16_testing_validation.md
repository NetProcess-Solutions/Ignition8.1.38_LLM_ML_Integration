# 16. Testing & Validation

145 tests passing, 2 skipped, 0 failing as of the v3.0 cut commit.
This chapter is the inventory: what the test suite covers, what it
doesn't, what's mocked vs in-memory vs real, and what the validation
gaps are heading into pilot.

The full test suite lives in
[service/tests/](service/tests/) and runs via `pytest`. Running it
locally:

```
cd service
pytest -q
# 145 passed, 2 skipped in 14.32s
```

## 16.1 Coverage by Service

| Area                        | File(s)                                            | Tests | Status |
|-----------------------------|----------------------------------------------------|-------|--------|
| Anchor parsing              | `test_anchor.py`, `test_anchor_regression.py`      | 28    | All pass |
| Anomaly detection           | `test_anomaly.py`                                  | 18    | All pass |
| Change ledger               | `test_change_ledger.py`                            | 22    | All pass |
| Chunker (structure-aware)   | `test_chunker.py`, `test_chunker_structured.py`    | 16    | All pass |
| Context assembler           | `test_context_assembler.py`, `test_context_assembler_v2.py` | 19 | All pass |
| Deviation tests             | `test_deviation.py`                                | 12    | All pass |
| Integration (cross-service) | `test_integrations_v2.py`                          | 8     | 2 skipped (require real Postgres) |
| LLM tool loop               | `test_llm_tool_loop.py`                            | 14    | All pass |
| Local LLM client            | `test_local_llm_client.py`                         | 12    | All pass |
| Percentiles + drift         | `test_percentiles.py`                              | 14    | All pass |
| Prompt regression           | `test_prompt_regression.py`                        | 6     | All pass |
| RCA chain (E2E)             | `test_rca_e2e.py`, `test_rca.py`                   | 17    | All pass |
| Retrieval (hybrid)          | `test_retrieval_hybrid.py` *(implied; full pipeline)*| 35   | All pass |
| **Total**                   |                                                    | **145+2 skipped** | **0 failing** |

## 16.2 What's Mocked, What's Real

The test suite is unit-and-integration-with-DB-mocked or
in-memory. Specifically:

- **Database.** Most tests use SQLAlchemy with SQLite in-memory; the
  schema-dependent integration tests (`test_integrations_v2.py`)
  expect a real Postgres + pgvector and are **skipped** by default.
  Run with `TEST_REAL_POSTGRES=1` to enable.
- **OpenAI.** All LLM-calling tests mock at the `OpenAI` client class
  level. The on-the-wire JSON shape is exercised; the LLM response
  itself is canned.
- **Embeddings.** Mocked. Tests compare against fixed embedding vectors
  injected at the seam.
- **Time.** `freezegun` for any test that depends on `datetime.utcnow`.
- **HTTP routes.** `httpx.AsyncClient` against the FastAPI app
  in-process — no real network.

## 16.3 What Is NOT Yet Tested

Honest gap inventory:

- **Real Postgres + pgvector roundtrip.** The 2 skipped integration
  tests in `test_integrations_v2.py` are the placeholders; they need
  a CI Postgres + pgvector image to run. The local pytest run
  exercises only mocked DB.
- **Real OpenAI API call.** Production smoke test — covered by the
  `/api/health/deep` endpoint at deploy time, not by pytest.
- **Real Ignition gateway pairing.** Integration smoke test — covered
  by the INSTALL.md Part 6 "send first chat" procedure, not pytest.
- **Load testing.** No formal load test bench. Pilot capacity
  (≤5 concurrent operators) is well below any plausible bottleneck;
  multi-instance horizontal-scale validation will be required when
  the second line is added.
- **Long-running soak.** No 7-day soak run. Memory/connection-pool
  leak risk is unmeasured.
- **Adversarial prompt injection.** The mitigations in chapter 14 §14.7
  are designed for, not formally adversarially tested.

## 16.4 The Eval Harness Path (B13)

[service/eval/harness.py](service/eval/harness.py) ships as a stub
with three `NotImplementedError`s:

```python
def replay_golden_case(case: dict) -> dict:
    """Replay a golden case end-to-end. Returns the response shape."""
    raise NotImplementedError("Build me when the golden corpus exists")

def score_citation_pr(response: dict, ground_truth: dict) -> dict:
    """Compute citation Precision/Recall against a labeled answer."""
    raise NotImplementedError("...")

def score_failure_mode_accuracy(response: dict, ground_truth: dict) -> dict:
    """Compare assistant FM classification to engineer-labeled FM."""
    raise NotImplementedError("...")
```

Each stub has full implementation notes inline. The **blocker** is
not the code; it's the absence of a labeled golden corpus. Build path:

1. Engineer hand-labels ~50 historical chat turns with:
   - Ground-truth correct answer
   - Ground-truth correct citations
   - Ground-truth correct failure-mode code
2. Run `replay_golden_case` against each, collect scored output
3. Compute aggregate citation P/R, FM accuracy, response similarity
4. Set CI threshold; gate prompt-version changes on green eval

This is a 1–2 week effort once the labeled corpus exists. The corpus
itself is the hard part — and is best built from observed pilot
traffic, not synthesized in advance.

## 16.5 Prompt Regression

`test_prompt_regression.py` exercises six "frozen" assistant responses
against `system_prompt_v2`. If the prompt is changed, these tests
will fail (responses will no longer be byte-identical). The intent is
**not** to lock the prompt; the intent is to surface that the prompt
changed so the eval harness (when present) can be re-run.

Currently the regression runs against canned LLM mock responses, not
real LLM output (because real LLM output has nondeterminism). When
B13 lands, the prompt regression suite shifts from "byte-identical
canned responses" to "above-threshold eval scores."

## 16.6 Failure-Mode Coverage of the Test Suite

The suite exercises:

- All 13 status values of `AnchorStatus`
- All 10 `message_feedback.signal_type` enum values
- All 4 `outcome_linkages.outcome_type` enum values
- All 4 confidence labels
- All 5 query classes through `should_use_rca_chain`
- All 5 tools' happy-path and timeout paths
- All 6 percentile scopes
- All 4 change-ledger delta types
- The empty-corpus, cold-start, and budget-exhausted paths through
  the RCA chain

The intentional gaps:

- Anomaly model fit on real `feature_snapshots` data (mocked because
  fitting on real data is non-deterministic and slow)
- Cross-encoder reranker (B2 stub) — no test because the implementation is a stub
- Symphony capture stream — no test because the implementation is a stub

## 16.7 Validation Plan for Pilot

Pre-go-live checks beyond pytest:

1. **Schema integrity.** Run `setup_database.sql` against a fresh
   Postgres, confirm all 30 tables, 5 views, 1 trigger present.
   Confirm `pg_partman` extension installed. Confirm `vector`
   extension version ≥ 0.7.
2. **Reference data seeded.** `seed_reference_data.sql` populates
   `failure_modes` (~25 codes), confirm row count.
3. **Initial line memory seeded.** `python -m service.scripts.seed_initial_data`
   succeeds; ~12 line-memory entries with `status='approved'`.
4. **Health check.** `GET /api/health/deep` returns 200 with `db: ok,
   embeddings: ok, llm: ok`.
5. **End-to-end smoke.** `POST /api/chat` with a real query returns a
   structured response with citations. Inspect `messages.context_snapshot`
   to confirm the full snapshot persisted.
6. **Outcome closure dry-run.** Manually invoke
   `services.outcome_closure.run_closure(window_hours=24)`, confirm
   `outcome_linkages` rows created (or empty, if no closeable turns).
7. **Audit immutability.** Attempt `UPDATE audit_log SET payload = '...'`
   from the service-role; confirm the trigger raises.

Procedure documented in [docs/runbook.md](docs/runbook.md).

## 16.8 Continuous-Integration Posture

The CI pipeline (CI engine is operator-choice; reference is GitHub
Actions YAML in `.github/workflows/ci.yml` if present in repo):

- On PR open/update: lint + pytest + type check (`mypy --strict
  service/`)
- On merge to main: full integration suite (`TEST_REAL_POSTGRES=1`)
- On tag: build container image, push to registry, deploy to staging,
  run `/api/health/deep` smoke

The deployable unit is the FastAPI service container; Postgres is
operator-provisioned (or compose-supplied for dev).

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Testing & Validation</p>
<p><span class="label">Stayed:</span> pytest-based unit + integration
suite. The intent to gate releases on green CI.</p>
<p><span class="label">Changed:</span> 145 tests passing (was ~92 at
v2.0 baseline). New coverage for: tool loop (B0.5), RCA chain (B8),
change ledger (B9), anomaly (B7), local LLM client (B12), prompt
regression. Documented mocked-vs-real boundary; documented integration
test skip + how to enable; documented the labeled-corpus blocker on B13.</p>
<p><span class="label">Considering:</span> A real-Postgres CI matrix
job (`pgvector/pgvector:pg16` container in CI). A 24-hour soak job in
staging. A "shadow traffic" diff harness — pipe a copy of prod queries
to a staging instance with a candidate prompt-version, diff the responses
offline. Adversarial prompt-injection corpus + automated red-team test.</p>
</div>
