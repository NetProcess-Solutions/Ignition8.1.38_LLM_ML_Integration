"""Sprint 3 / B0.4 — Tools registry tests.

Verifies the registry shape and the tool-call dispatch protocol. Live
DB-backed tools are exercised via the cache seam in test_percentiles.py;
here we focus on:

  * Every tool has a valid OpenAI spec.
  * Unknown tool name returns a structured error, not a raise.
  * Bad arguments produce ok=False with an error.
  * `to_llm_json()` round-trips and includes citation_id when present.
"""
from __future__ import annotations

import json

import pytest

from services import percentiles
from services.percentiles import Scope
from services.tools import TOOLS, ToolResult, call_tool, openai_tool_specs


def test_every_tool_has_complete_openai_spec() -> None:
    for name, spec in TOOLS.items():
        oai = spec.openai_spec()
        assert oai["type"] == "function"
        fn = oai["function"]
        assert fn["name"] == name
        assert fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        assert "properties" in params
        # All required keys must exist in properties.
        for req in params.get("required", []):
            assert req in params["properties"], (name, req)


def test_openai_tool_specs_supports_allowlist() -> None:
    only = openai_tool_specs(allowlist={"percentile_of"})
    assert len(only) == 1
    assert only[0]["function"]["name"] == "percentile_of"


@pytest.mark.asyncio
async def test_unknown_tool_returns_error() -> None:
    res = await call_tool("does_not_exist", {})
    assert res.ok is False
    assert "unknown tool" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_percentile_tool_via_seeded_cache(monkeypatch) -> None:
    """End-to-end through `call_tool` → tool handler → percentiles.

    We avoid the real DB by:
      1. Seeding the percentiles cache for the chosen scope so the SQL
         path is never taken.
      2. Replacing the SessionFactory with a no-op async context manager
         so tools.call_tool can `async with` it without a real DB.
    """
    scope = Scope(kind="style", product_style="S-TEST")
    percentiles._seed_cache("Front2_Temp", scope, list(range(0, 100)))

    class _NoopSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr("services.tools.SessionFactory", _NoopSession)

    res = await call_tool(
        "percentile_of",
        {
            "tag": "Front2_Temp",
            "value": 50,
            "scope": {"kind": "style", "product_style": "S-TEST"},
        },
    )
    assert res.ok, res.error
    assert res.citation is not None
    assert res.citation.type == "DISTRIBUTION"
    payload = json.loads(res.to_llm_json())
    assert payload["ok"] is True
    assert payload["data"]["sample_size"] == 100
    assert "citation_id" in payload


@pytest.mark.asyncio
async def test_bad_arguments_return_structured_error(monkeypatch) -> None:
    class _NoopSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr("services.tools.SessionFactory", _NoopSession)
    res = await call_tool("percentile_of", {"value": 50})  # tag missing
    assert res.ok is False
    assert res.error is not None
