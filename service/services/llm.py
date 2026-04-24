"""LLM client abstraction. Swap providers by changing LLM_PROVIDER env var."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import structlog
from openai import AsyncAzureOpenAI, AsyncOpenAI

from config.settings import get_settings

_log = structlog.get_logger(__name__)


# Module-level concurrency limit across ALL LLM calls in the process.
# Lazy-initialized so the loop exists when first awaited.
_LLM_SEM: asyncio.Semaphore | None = None


def _get_sem() -> asyncio.Semaphore:
    global _LLM_SEM
    if _LLM_SEM is None:
        _LLM_SEM = asyncio.Semaphore(get_settings().llm_max_concurrency)
    return _LLM_SEM


def _httpx_timeout() -> httpx.Timeout:
    s = get_settings()
    return httpx.Timeout(s.llm_request_timeout_s, connect=s.llm_connect_timeout_s)


@dataclass
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class ToolCallTrace:
    """Per-iteration record of a tool call the LLM requested."""

    name: str
    arguments: dict[str, Any]
    result_json: str  # raw JSON returned to the model
    citation_id: str | None = None


@dataclass
class ToolEnabledResponse:
    """Result of `complete_with_tools` — final answer + full tool trace."""

    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    iterations: int
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    citations_collected: list[Any] = field(default_factory=list)  # SourceCitation


class LLMClient(Protocol):
    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    async def complete_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, Any]],
        max_iters: int = 3,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ToolEnabledResponse: ...

    @property
    def model_name(self) -> str: ...


class OpenAIChatClient:
    def __init__(self) -> None:
        s = get_settings()
        self._client = AsyncOpenAI(
            api_key=s.openai_api_key,
            timeout=_httpx_timeout(),
        )
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
        async with _get_sem():
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

    async def complete_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, object]],
        max_iters: int = 3,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ToolEnabledResponse:
        return await _run_tool_loop(
            openai_client=self._client,
            model=self._model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=tools,
            max_iters=max_iters,
            temperature=temperature if temperature is not None else self._default_temp,
            max_tokens=max_tokens if max_tokens is not None else self._default_max,
            model_label=self._model,
        )


class AzureOpenAIChatClient:
    def __init__(self) -> None:
        s = get_settings()
        self._client = AsyncAzureOpenAI(
            azure_endpoint=s.azure_openai_endpoint,
            api_key=s.azure_openai_api_key,
            api_version=s.azure_openai_api_version,
            timeout=_httpx_timeout(),
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
        async with _get_sem():
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

    async def complete_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, object]],
        max_iters: int = 3,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ToolEnabledResponse:
        return await _run_tool_loop(
            openai_client=self._client,
            model=self._deployment,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=tools,
            max_iters=max_iters,
            temperature=temperature if temperature is not None else self._default_temp,
            max_tokens=max_tokens if max_tokens is not None else self._default_max,
            model_label=f"azure:{self._deployment}",
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
    elif s.llm_provider == "local":
        _client = LocalOpenAICompatibleClient()
    else:
        raise NotImplementedError(f"LLM provider '{s.llm_provider}' not supported")
    return _client


class LocalOpenAICompatibleClient:
    """OpenAI-compatible HTTP client (vLLM, llama.cpp server, LM Studio, etc.).

    Sprint 7+ / B12 — air-gap fallback. Configure via:
        local_llm_endpoint = "http://localhost:8001/v1"
        local_llm_model    = "Qwen/Qwen2.5-7B-Instruct"
        local_llm_api_key  = "EMPTY"   # most local servers ignore this
    """

    def __init__(self) -> None:
        s = get_settings()
        if not s.local_llm_endpoint:
            raise RuntimeError(
                "local_llm_endpoint must be set when LLM_PROVIDER=local"
            )
        if not s.local_llm_model:
            raise RuntimeError(
                "local_llm_model must be set when LLM_PROVIDER=local"
            )
        self._client = AsyncOpenAI(
            api_key=s.local_llm_api_key or "EMPTY",
            base_url=s.local_llm_endpoint,
            timeout=_httpx_timeout(),
        )
        self._model = s.local_llm_model
        self._default_temp = s.openai_temperature
        self._default_max = s.openai_max_tokens

    @property
    def model_name(self) -> str:
        return f"local:{self._model}"

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        async with _get_sem():
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
            model=self.model_name,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
        )

    async def complete_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, Any]],
        max_iters: int = 3,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ToolEnabledResponse:
        # vLLM ≥0.6 supports OpenAI tool-calling; older servers degrade
        # gracefully because the loop falls back to a forced final answer
        # when the model doesn't emit tool_calls.
        return await _run_tool_loop(
            openai_client=self._client,
            model=self._model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=tools,
            max_iters=max_iters,
            temperature=temperature if temperature is not None else self._default_temp,
            max_tokens=max_tokens if max_tokens is not None else self._default_max,
            model_label=self.model_name,
        )


# ---------------------------------------------------------------------------
# Shared tool-calling loop (B0.5)
# ---------------------------------------------------------------------------

async def _run_tool_loop(
    *,
    openai_client: Any,
    model: str,
    system_prompt: str,
    user_prompt: str,
    tools: list[dict[str, Any]],
    max_iters: int,
    temperature: float,
    max_tokens: int,
    model_label: str,
) -> ToolEnabledResponse:
    """
    Drive an OpenAI tool-calling loop. Imported lazily here to avoid a
    circular import (services.tools imports SourceCitation, which is fine,
    but the call_tool dispatcher lives in services.tools).
    """
    from services.tools import call_tool  # local import — break cycle

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    trace: list[ToolCallTrace] = []
    citations: list[Any] = []
    total_prompt = total_completion = total_total = 0
    iters = 0

    for iters in range(1, max_iters + 1):
        async with _get_sem():
            resp = await openai_client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=temperature,
                max_tokens=max_tokens,
            )
        usage = resp.usage
        if usage:
            total_prompt     += usage.prompt_tokens
            total_completion += usage.completion_tokens
            total_total      += usage.total_tokens

        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        if not tool_calls:
            return ToolEnabledResponse(
                content=msg.content or "",
                model=model_label,
                prompt_tokens=total_prompt,
                completion_tokens=total_completion,
                total_tokens=total_total,
                iterations=iters,
                tool_calls=trace,
                citations_collected=citations,
            )

        # Echo the assistant message (with tool_calls) back into context.
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        })

        # Execute each tool the model asked for. Bounded by max_iters.
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as e:
                _log.warn("tool_args_bad_json", tool=tc.function.name, err=str(e))
                args = {}
            result = await call_tool(tc.function.name, args)
            result_json = result.to_llm_json()
            trace.append(ToolCallTrace(
                name=tc.function.name,
                arguments=args,
                result_json=result_json,
                citation_id=result.citation.id if result.citation else None,
            ))
            if result.citation is not None:
                citations.append(result.citation)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_json,
            })

    # Hit max_iters without a final assistant message → force one more turn
    # without tools so the model has to commit to an answer.
    async with _get_sem():
        final = await openai_client.chat.completions.create(
            model=model,
            messages=messages + [{
                "role": "user",
                "content": "Tool budget exhausted. Provide your final answer now using ONLY the evidence already gathered.",
            }],
            temperature=temperature,
            max_tokens=max_tokens,
        )
    if final.usage:
        total_prompt     += final.usage.prompt_tokens
        total_completion += final.usage.completion_tokens
        total_total      += final.usage.total_tokens
    return ToolEnabledResponse(
        content=final.choices[0].message.content or "",
        model=model_label,
        prompt_tokens=total_prompt,
        completion_tokens=total_completion,
        total_tokens=total_total,
        iterations=iters,
        tool_calls=trace,
        citations_collected=citations,
    )
