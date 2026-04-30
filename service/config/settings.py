"""Application configuration loaded from environment variables."""
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Default values that must NOT survive into a production deployment.
DEFAULT_DEV_API_KEYS = {"dev-key-change-me", "change-me", "test", ""}
DEFAULT_DB_PASSWORD_MARKERS = ("change_me_in_production",)
MIN_PRODUCTION_API_KEY_LEN = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Service
    service_host: str = "0.0.0.0"
    service_port: int = 8000
    service_log_level: str = "INFO"
    service_env: Literal["development", "staging", "production"] = "development"
    api_key: str = Field(default="dev-key-change-me", min_length=8)

    # Database
    database_url: str = (
        "postgresql+asyncpg://chatbot:change_me_in_production@postgres:5432/ignition_chatbot"
    )
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # Data plane backend (three-plane architecture, see
    # docs/THREE_PLANE_ARCHITECTURE.md). The harness depends on the
    # `DataPlane` Protocol in `db/data_plane.py`; this switch chooses
    # the implementation. `databricks` is a placeholder pending IT
    # confirmation of platform + credentials.
    data_plane_backend: Literal["postgres", "databricks"] = "postgres"

    # LLM
    llm_provider: Literal["openai", "azure_openai", "local"] = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_temperature: float = 0.1
    openai_max_tokens: int = 1500
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-08-01-preview"

    # LLM resilience (Sprint 1 / A1)
    llm_request_timeout_s: float = 30.0
    llm_connect_timeout_s: float = 5.0
    llm_max_concurrency: int = 6

    # Embeddings
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimension: int = 384

    # Retrieval
    retrieval_top_k: int = 10
    retrieval_min_score: float = 0.30
    retrieval_recent_events_hours: int = 72
    memory_top_k: int = 5
    document_weight_min: float = 0.5
    document_weight_max: float = 1.5

    # Per-user chat rate limiting (Sprint 1 / A1)
    chat_rate_per_user_per_min: int = 30
    chat_rate_per_user_per_day: int = 500
    daily_token_budget_per_user: int = 200_000

    # Active prompt names (looked up in prompt_versions table)
    active_system_prompt_name: str = "system_prompt"
    active_context_template_name: str = "chat_context_template"

    # v2.0 nightly integrations (design sections 7.2 / 7.3)
    scheduler_enabled: bool = False
    nightly_jobs_interval_seconds: int = 86400
    wo_sync_enabled: bool = False
    ignition_wo_db_url: str = ""
    symphony_backfill_enabled: bool = False

    # Identity / authZ (Sprint 1 / A4)
    gateway_hmac_secret: str = ""
    gateway_token_max_age_s: int = 300
    gateway_id_allowlist: str = ""  # comma-separated; empty = allow any
    require_user_token: bool = False  # forced True in production by guard

    # Hybrid retrieval (Sprint 3 / B1)
    retrieval_mode: Literal["vector", "hybrid"] = "hybrid"
    retrieval_keyword_top_k: int = 30
    retrieval_rrf_k: int = 60
    retrieval_failure_mode_boost: float = 1.5
    retrieval_equipment_boost: float = 1.3
    retrieval_mmr_lambda: float = 0.7  # 1.0 = pure relevance, 0.0 = pure diversity
    retrieval_mmr_enabled: bool = True

    # RCA reasoning chain (Sprint 4 / B8)
    rca_chain_enabled: bool = True
    rca_step1_max_iters: int = 2
    rca_step2_max_iters: int = 2
    rca_max_hypotheses: int = 3
    rca_max_evidence_per_hypothesis: int = 5
    rca_max_total_tool_calls: int = 15
    rca_step1_model: str = ""  # empty -> use default openai_model
    rca_step2_model: str = ""  # empty -> use default openai_model
    rca_cache_ttl_seconds: int = 300

    # Local LLM client (Sprint 7+ / B12) — OpenAI-compatible HTTP server
    local_llm_endpoint: str = ""    # e.g. "http://localhost:8001/v1"
    local_llm_model: str = ""
    local_llm_api_key: str = "EMPTY"

    # Multivariate anomaly (Sprint 5 / B7)
    anomaly_enabled: bool = True
    anomaly_min_history_runs: int = 30
    anomaly_p95_z_threshold: float = 3.0

    # Outcome closure (Sprint 6 / B10)
    outcome_followup_hours: int = 24
    outcome_closure_enabled: bool = True

    def collect_production_violations(self) -> list[str]:
        """Return human-readable reasons this config is unsafe for prod.

        Empty list = production-ready. Used by `assert_production_ready`
        and exposed for unit testing without raising.
        """
        violations: list[str] = []
        if self.api_key in DEFAULT_DEV_API_KEYS:
            violations.append("api_key is the default dev key")
        if len(self.api_key) < MIN_PRODUCTION_API_KEY_LEN:
            violations.append(
                f"api_key is shorter than {MIN_PRODUCTION_API_KEY_LEN} chars"
            )
        for marker in DEFAULT_DB_PASSWORD_MARKERS:
            if marker in self.database_url:
                violations.append(
                    f"database_url contains the default marker '{marker}'"
                )
        if self.llm_provider == "openai" and not self.openai_api_key:
            violations.append("llm_provider=openai but OPENAI_API_KEY is empty")
        if self.llm_provider == "azure_openai" and not self.azure_openai_api_key:
            violations.append(
                "llm_provider=azure_openai but AZURE_OPENAI_API_KEY is empty"
            )
        if not self.gateway_hmac_secret or len(self.gateway_hmac_secret) < 32:
            violations.append(
                "gateway_hmac_secret is missing or shorter than 32 chars"
            )
        return violations

    def assert_production_ready(self) -> None:
        """Raise RuntimeError if running in production with unsafe defaults."""
        if self.service_env != "production":
            return
        violations = self.collect_production_violations()
        if violations:
            raise RuntimeError(
                "Refusing to start in production with unsafe configuration:\n  - "
                + "\n  - ".join(violations)
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
