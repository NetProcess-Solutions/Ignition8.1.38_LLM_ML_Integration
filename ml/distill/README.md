# B6 — Knowledge distillation (PLACEHOLDER)

Distill the cloud model's behavior on **specific narrow tasks** into a
~1-3 B parameter student that runs on a CPU at the edge. Two tasks
are good candidates here:

1. **Failure-mode classifier** — query + curated context → one of the
   ~30 known failure modes (or "unknown"). Already partially served by
   `services/failure_mode_classifier.py` but currently uses heuristics;
   distillation would replace those with a learned classifier.

2. **Anchor parser** — natural-language query → `QueryAnchor` JSON.
   Currently regex-based in `services/anchor.py`; works fine for
   ~80% of queries but misses paraphrases. A distilled student would
   handle "what made the line eat itself yesterday" → past_event
   without explicit keywords.

## Pipeline (each step is a script in ml/distill/)

### 1. `gather_teacher_outputs.py`
For ~10k unlabeled queries (sampled from real `messages.user_query`),
ask the cloud model to produce the target output. For task 1 this is
a single-token classification; for task 2 this is the parsed JSON.

### 2. `train_student.py`
Use `transformers` + a small base (e.g. `roberta-base` for task 1,
`Phi-3-mini` for task 2). Standard supervised training.

### 3. `eval.py`
Hold out 10% of the queries, compare student vs. teacher and student
vs. ground-truth (if any).

### 4. `serve.py`
Wrap the student in an in-process pipeline so `services/anchor.py` and
`services/failure_mode_classifier.py` can call it without an HTTP hop.

## When to build

Only after the eval harness (B4) shows the heuristic versions are
hurting precision in production. Distillation is the right answer
when you have **enough query volume** that running the full LLM for
classification is wasteful and **predictable enough latency
requirements** that a millisecond CPU classifier matters.

If neither is true, a fine-tuned `gpt-4o-mini` call will be cheaper
to operate.
