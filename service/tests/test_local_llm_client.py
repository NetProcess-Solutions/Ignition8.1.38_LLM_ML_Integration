"""Sprint 7 / B12 — local OpenAI-compatible LLM client smoke tests."""
from __future__ import annotations

from services.llm import LocalOpenAICompatibleClient, get_llm_client


def test_local_client_requires_endpoint(monkeypatch):
    from config.settings import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "local_llm_endpoint", "")
    monkeypatch.setattr(s, "local_llm_model", "qwen")
    import pytest
    with pytest.raises(RuntimeError, match="local_llm_endpoint"):
        LocalOpenAICompatibleClient()


def test_local_client_requires_model(monkeypatch):
    from config.settings import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "local_llm_endpoint", "http://localhost:8001/v1")
    monkeypatch.setattr(s, "local_llm_model", "")
    import pytest
    with pytest.raises(RuntimeError, match="local_llm_model"):
        LocalOpenAICompatibleClient()


def test_local_client_model_name_is_prefixed(monkeypatch):
    from config.settings import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "local_llm_endpoint", "http://localhost:8001/v1")
    monkeypatch.setattr(s, "local_llm_model", "Qwen/Qwen2.5-7B-Instruct")
    monkeypatch.setattr(s, "local_llm_api_key", "EMPTY")
    c = LocalOpenAICompatibleClient()
    assert c.model_name == "local:Qwen/Qwen2.5-7B-Instruct"


def test_get_llm_client_dispatches_local_provider(monkeypatch):
    from config.settings import get_settings
    import services.llm as llm_mod
    monkeypatch.setattr(llm_mod, "_client", None)
    s = get_settings()
    monkeypatch.setattr(s, "llm_provider", "local")
    monkeypatch.setattr(s, "local_llm_endpoint", "http://localhost:8001/v1")
    monkeypatch.setattr(s, "local_llm_model", "x")
    monkeypatch.setattr(s, "local_llm_api_key", "EMPTY")
    client = get_llm_client()
    assert isinstance(client, LocalOpenAICompatibleClient)
    # Reset module-level singleton to avoid leaking into other tests
    monkeypatch.setattr(llm_mod, "_client", None)
