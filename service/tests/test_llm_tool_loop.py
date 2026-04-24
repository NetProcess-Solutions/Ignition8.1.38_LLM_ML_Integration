"""Sprint 3 / B0.5 — `_run_tool_loop` unit tests with a stub OpenAI client.

Drives the loop without hitting the network. The stub OpenAI client
returns canned responses keyed by call index: first a tool_call, then a
final assistant message. We assert the loop:
  * surfaces the final content,
  * accumulates token usage across iterations,
  * records every tool call in the trace,
  * collects citations from successful tool results.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from models.schemas import SourceCitation
from services import llm as llm_mod
from services.llm import _run_tool_loop


# -----------------------------------------------------------------------------
# Fakes
# -----------------------------------------------------------------------------
@dataclass
class _Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


def _fake_choice(content: str | None = None, tool_calls: list[Any] | None = None) -> Any:
    msg = SimpleNamespace(content=content, tool_calls=tool_calls or None)
    return SimpleNamespace(message=msg)


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> Any:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


class FakeOpenAI:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._responses.pop(0)


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tool_loop_executes_tool_then_returns_final(monkeypatch) -> None:
    # Stub the registry's call_tool to return a predictable result.
    fake_citation = SourceCitation(
        id="c1", type="DISTRIBUTION", title="t", excerpt="e",
    )

    async def _fake_call_tool(name: str, args: dict[str, Any]):
        from services.tools import ToolResult
        return ToolResult(ok=True, data={"echo": args}, citation=fake_citation)

    monkeypatch.setattr("services.tools.call_tool", _fake_call_tool)

    responses = [
        # Iteration 1: model requests percentile_of(...)
        SimpleNamespace(
            choices=[_fake_choice(
                content=None,
                tool_calls=[_tool_call("call_1", "percentile_of",
                                       {"tag": "Front2_Temp", "value": 198})],
            )],
            usage=_Usage(100, 5, 105),
        ),
        # Iteration 2: model returns a final answer (no tool_calls)
        SimpleNamespace(
            choices=[_fake_choice(content="Final answer with citation [c1].")],
            usage=_Usage(120, 30, 150),
        ),
    ]
    fake = FakeOpenAI(responses)

    res = await _run_tool_loop(
        openai_client=fake,
        model="gpt-4o-mini",
        system_prompt="sys",
        user_prompt="user",
        tools=[{"type": "function", "function": {"name": "percentile_of"}}],
        max_iters=3,
        temperature=0.0,
        max_tokens=512,
        model_label="gpt-4o-mini",
    )

    assert res.content == "Final answer with citation [c1]."
    assert res.iterations == 2
    assert res.prompt_tokens == 220
    assert res.completion_tokens == 35
    assert res.total_tokens == 255
    assert len(res.tool_calls) == 1
    tc = res.tool_calls[0]
    assert tc.name == "percentile_of"
    assert tc.arguments == {"tag": "Front2_Temp", "value": 198}
    assert tc.citation_id == "c1"
    assert len(res.citations_collected) == 1
    # Verify the assistant tool_calls echo + role=tool message were appended.
    second_call_messages = fake.calls[1]["messages"]
    roles = [m["role"] for m in second_call_messages]
    assert roles[-2:] == ["assistant", "tool"]


@pytest.mark.asyncio
async def test_tool_loop_handles_immediate_final_answer() -> None:
    responses = [SimpleNamespace(
        choices=[_fake_choice(content="Direct answer, no tools needed.")],
        usage=_Usage(50, 10, 60),
    )]
    fake = FakeOpenAI(responses)
    res = await _run_tool_loop(
        openai_client=fake,
        model="m",
        system_prompt="s", user_prompt="u",
        tools=[],
        max_iters=3,
        temperature=0.0,
        max_tokens=128,
        model_label="m",
    )
    assert res.iterations == 1
    assert res.content == "Direct answer, no tools needed."
    assert res.tool_calls == []
    assert res.total_tokens == 60


@pytest.mark.asyncio
async def test_tool_loop_max_iters_forces_final_answer(monkeypatch) -> None:
    """If the model keeps requesting tools forever, the loop must stop and
    force a final answer rather than burning unbounded tokens."""

    async def _fake_call_tool(name: str, args: dict[str, Any]):
        from services.tools import ToolResult
        return ToolResult(ok=True, data={}, citation=None)

    monkeypatch.setattr("services.tools.call_tool", _fake_call_tool)

    # max_iters=2 → 2 iterations of tool requests + 1 forced final call
    looping_response = SimpleNamespace(
        choices=[_fake_choice(
            content=None,
            tool_calls=[_tool_call("c", "percentile_of", {"tag": "x", "value": 1})],
        )],
        usage=_Usage(10, 1, 11),
    )
    final_response = SimpleNamespace(
        choices=[_fake_choice(content="Forced final answer.")],
        usage=_Usage(20, 5, 25),
    )
    fake = FakeOpenAI([looping_response, looping_response, final_response])

    res = await _run_tool_loop(
        openai_client=fake,
        model="m",
        system_prompt="s", user_prompt="u",
        tools=[{"type": "function", "function": {"name": "percentile_of"}}],
        max_iters=2,
        temperature=0.0,
        max_tokens=128,
        model_label="m",
    )
    assert res.content == "Forced final answer."
    assert len(res.tool_calls) == 2
    # Forced-final user message should appear in the final call's messages.
    forced_msgs = fake.calls[-1]["messages"]
    assert "Tool budget exhausted" in forced_msgs[-1]["content"]
