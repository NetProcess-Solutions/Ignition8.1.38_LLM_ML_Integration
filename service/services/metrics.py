"""
Custom Prometheus metrics for the chat service (Sprint 1 / A2).

The standard HTTP RED metrics are provided by
prometheus-fastapi-instrumentator. The metrics here capture
domain-specific signals:

- chat_short_circuit_total{reason}     — refusals that did not call the LLM
- chat_confidence_total{label}         — final confidence label distribution
- llm_tokens_total{model,direction}    — prompt/completion token counters
- llm_cost_usd_total{model}            — cumulative cost from a static price map
- retrieval_latency_seconds{stage}     — per-stage retrieval latency
- chat_in_flight                       — concurrent in-flight chat requests
- chat_total_seconds                   — end-to-end chat latency

Cost prices are a static, conservative best-effort map; missing models log
once and are skipped (no metric increment).
"""
from __future__ import annotations

import structlog
from prometheus_client import Counter, Gauge, Histogram


_log = structlog.get_logger(__name__)

chat_short_circuit_total = Counter(
    "chat_short_circuit_total",
    "Chat queries that returned without calling the LLM, by reason.",
    ["reason"],
)

chat_confidence_total = Counter(
    "chat_confidence_total",
    "Final confidence label of every assistant response.",
    ["label"],
)

llm_tokens_total = Counter(
    "llm_tokens_total",
    "Cumulative LLM tokens by model and direction.",
    ["model", "direction"],
)

llm_cost_usd_total = Counter(
    "llm_cost_usd_total",
    "Cumulative LLM cost in USD by model.",
    ["model"],
)

retrieval_latency_seconds = Histogram(
    "retrieval_latency_seconds",
    "Retrieval stage latency in seconds.",
    ["stage"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

chat_in_flight = Gauge(
    "chat_in_flight",
    "Number of chat requests currently being processed.",
)

chat_total_seconds = Histogram(
    "chat_total_seconds",
    "End-to-end /api/chat latency in seconds.",
    buckets=(0.25, 0.5, 1.0, 2.5, 5.0, 7.5, 10.0, 15.0, 30.0),
)

# Sprint 5 / B1 — track which retrieval mode each query took.
retrieval_mode_used = Counter(
    "retrieval_mode_used_total",
    "Document-retrieval mode chosen per query.",
    ["mode"],  # "hybrid" or "vector"
)

# Sprint 4 / B8 — track RCA chain dispatch + cache hits.
rca_chain_total = Counter(
    "rca_chain_total",
    "RCA two-step chain runs by outcome.",
    ["outcome"],  # "completed" | "failed" | "cache_hit_step1"
)


# Static price map: USD per 1M tokens. Update as providers change pricing.
# direction: 'prompt' or 'completion'.
_PRICE_PER_M_TOKENS: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60},
    "gpt-4o":      {"prompt": 2.50, "completion": 10.00},
    "gpt-4.1-mini": {"prompt": 0.40, "completion": 1.60},
    "gpt-4.1":      {"prompt": 2.00, "completion": 8.00},
    # Azure deployments are user-named; price entry is keyed by the
    # underlying model where known.
}
_MISSING_PRICE_LOGGED: set[str] = set()


def _price_lookup(model: str) -> dict[str, float] | None:
    if model in _PRICE_PER_M_TOKENS:
        return _PRICE_PER_M_TOKENS[model]
    # Try stripping an "azure:" prefix.
    if model.startswith("azure:"):
        sub = model.split(":", 1)[1]
        if sub in _PRICE_PER_M_TOKENS:
            return _PRICE_PER_M_TOKENS[sub]
    return None


def record_llm_usage(
    model: str, prompt_tokens: int, completion_tokens: int
) -> None:
    """Increment token counters and (when known) cost counters."""
    llm_tokens_total.labels(model=model, direction="prompt").inc(prompt_tokens)
    llm_tokens_total.labels(model=model, direction="completion").inc(completion_tokens)
    prices = _price_lookup(model)
    if prices is None:
        if model not in _MISSING_PRICE_LOGGED:
            _MISSING_PRICE_LOGGED.add(model)
            _log.warning("llm_price_unknown", model=model)
        return
    cost = (
        prompt_tokens / 1_000_000 * prices["prompt"]
        + completion_tokens / 1_000_000 * prices["completion"]
    )
    llm_cost_usd_total.labels(model=model).inc(cost)
