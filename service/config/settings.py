"""Application configuration loaded from environment variables."""
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Embeddings
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimension: int = 384

    # Retrieval
    retrieval_top_k: int = 10
    retrieval_min_score: float = 0.30
    retrieval_recent_events_hours: int = 72
    memory_top_k: int = 5

    # Active prompt names (looked up in prompt_versions table)
    active_system_prompt_name: str = "system_prompt"
    active_context_template_name: str = "chat_context_template"


@lru_cache
def get_settings() -> Settings:
    return Settings()
