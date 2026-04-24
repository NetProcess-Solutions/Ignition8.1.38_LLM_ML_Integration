# B5 — Fine-tuning data prep (PLACEHOLDER)

Dataset construction for fine-tuning a smaller open model (e.g.
Qwen2.5-7B, Llama-3.1-8B) on the plant's own RCA traces.

## When to build this

After ~3 months of production with the cloud LLM, you'll have:
  * Several thousand `messages` rows with persisted `context_snapshot`
    (the assembled prompt) + assistant `content` (the answer)
  * `outcome_linkages` rows that flag which answers were
    `confirmed` / `rejected` by operators
  * `feedback_signals` for additional quality labels

Mining only the `confirmed` answers gives you a reasonable supervised
fine-tuning corpus that captures plant-specific terminology, recipes,
and reasoning patterns the cloud model approximates from generic
manufacturing text.

## Required steps (each is a script in this folder)

### 1. `extract_pairs.py`
Read from Postgres:
```sql
SELECT m.context_snapshot, m.content
FROM messages m
JOIN outcome_linkages ol ON ol.message_id = m.id
WHERE m.role = 'assistant'
  AND ol.alignment = 'confirmed'
  AND m.created_at >= NOW() - INTERVAL '6 months'
```
Reconstruct the (system_prompt, user_block, assistant_response) tuple
from `context_snapshot`. Strip PII (operator names, badge numbers).

### 2. `redact.py`
Replace any internal IDs that aren't generalizable (specific WO
numbers, customer names, internal acronyms) with stable tokens
(`<WO_ID_1>`, `<CUSTOMER_A>`). The model should learn the *pattern*,
not memorize specific numbers.

### 3. `format_chatml.py`
Emit JSONL in the chat template of the chosen base model. Example for
Qwen2.5:
```jsonl
{"messages": [
  {"role": "system", "content": "..."},
  {"role": "user",   "content": "..."},
  {"role": "assistant", "content": "..."}
]}
```

### 4. `split.py`
Hold out 10% as eval, stratified by failure mode so every mode is
represented in both splits.

### 5. Train (out of scope for this repo)
Use Axolotl, LLaMA-Factory, or Unsloth on a single H100 / A100. ~3
epochs, LR 1e-5, LoRA r=16 is a sensible starting point.

### 6. Deploy
Drop the merged model into a vLLM container, point
`local_llm_endpoint` at it, set `llm_provider=local`. The B12 client
already routes correctly.

## Privacy / data-governance pre-flight

Before you ship any of this:
  * Get sign-off that production query/response logs may leave
    Postgres (even to your own training box).
  * Confirm no PHI / customer secrets are in `context_snapshot` —
    inspect 100 random rows manually first.
  * Decide retention: do you keep the raw extracted JSONL forever, or
    re-extract per training run? The latter is safer.
