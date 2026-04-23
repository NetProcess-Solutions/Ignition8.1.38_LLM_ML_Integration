"""LLM client abstraction. Swap providers by changing LLM_PROVIDER env var."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from openai import AsyncAzureOpenAI, AsyncOpenAI

from config.settings import get_settings


@dataclass
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class LLMClient(Protocol):
    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    @property
    def model_name(self) -> str: ...


class OpenAIChatClient:
    def __init__(self) -> None:
        s = get_settings()
        self._client = AsyncOpenAI(api_key=s.openai_api_key)
        self._model = s.openai_model
        self._default_temp = s.openai_temperature
        self._default_max = s.openai_max_tokens

    @property
    def model_name(self) -> str:
        return self._model

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature if temperature is not None else self._default_temp,
            max_tokens=max_tokens if max_tokens is not None else self._default_max,
        )
        choice = resp.choices[0]
        usage = resp.usage
        return LLMResponse(
            content=choice.message.content or "",
            model=resp.model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
        )


class AzureOpenAIChatClient:
    def __init__(self) -> None:
        s = get_settings()
        self._client = AsyncAzureOpenAI(
            azure_endpoint=s.azure_openai_endpoint,
            api_key=s.azure_openai_api_key,
            api_version=s.azure_openai_api_version,
        )
        self._deployment = s.azure_openai_deployment
        self._default_temp = s.openai_temperature
        self._default_max = s.openai_max_tokens

    @property
    def model_name(self) -> str:
        return f"azure:{self._deployment}"

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        resp = await self._client.chat.completions.create(
            model=self._deployment,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature if temperature is not None else self._default_temp,
            max_tokens=max_tokens if max_tokens is not None else self._default_max,
        )
        choice = resp.choices[0]
        usage = resp.usage
        return LLMResponse(
            content=choice.message.content or "",
            model=f"azure:{self._deployment}",
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
        )


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is not None:
        return _client
    s = get_settings()
    if s.llm_provider == "openai":
        _client = OpenAIChatClient()
    elif s.llm_provider == "azure_openai":
        _client = AzureOpenAIChatClient()
    else:
        raise NotImplementedError(f"LLM provider '{s.llm_provider}' not supported")
    return _client
