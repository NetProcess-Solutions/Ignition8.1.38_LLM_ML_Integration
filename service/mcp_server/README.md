# MCP tool server (`coater1-tools`)

Wraps the deterministic tool layer
([`service/services/tools.py`](../services/tools.py)) behind the
**Model Context Protocol** so the same functions can be called by any
MCP-compatible client — this advisor today, other Shaw AI projects
tomorrow.

This is the **scaffold** for the three-plane architecture described in
[`docs/THREE_PLANE_ARCHITECTURE.md`](../../docs/THREE_PLANE_ARCHITECTURE.md).
It is intentionally thin and reuses the existing tool implementations
verbatim — no logic duplication.

## Why this exists

The director's "two-flavor script split":

1. **Standard functions / methods** → wrapped as MCP tool servers,
   hosted centrally so other Shaw projects can reuse them.
2. **Orchestration / order of operations** → agentic harness.

The tools exposed here are flavor 1. The agentic harness in
[`service/services/rag.py`](../services/rag.py) is flavor 2.

## Inspect without installing the MCP SDK

```
python -m mcp_server.server --list
```

Prints the JSON manifest of exposed tools (names, descriptions, input
schemas) so you can show IT what the server would advertise without
needing the SDK installed.

## Run locally

Install the SDK:

```
pip install mcp
```

Then start the server over stdio (the default MCP transport, used by
IDE clients like Claude Desktop and editor integrations):

```
python -m mcp_server.server
```

Or over HTTP/SSE for hosted deployment:

```
pip install "mcp[sse]" starlette uvicorn
python -m mcp_server.server --transport sse --host 0.0.0.0 --port 8765
```

## What's exposed

Every tool in `services.tools.TOOLS`:

- `percentile_of`
- `compare_to_distribution`
- `nearest_historical_runs`
- `detect_drift`
- `defect_events_in_window`

When new tools are added to the registry, they appear here automatically
— no edits to `server.py` required.

## Read-only by construction

There is no `write_*` tool, no DB-mutating tool, and no file-system tool
in the registry. The MCP transport adds no new authority; it is a
different way to call the same read-only functions.

## What the harness will do next

Once IT confirms a hosting path, the agentic harness will:

1. Call this server over SSE/HTTP instead of in-process.
2. Continue to enforce the per-call budget and citation discipline at
   the orchestration layer.
3. Treat tool-server outages as "evidence insufficient" → templated
   refusal, no model call.

The migration is reversible — the in-process path remains as a fallback.
