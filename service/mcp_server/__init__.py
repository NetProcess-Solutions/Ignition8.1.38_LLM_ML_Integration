"""
MCP tool-server scaffold.

This package wraps the deterministic tools in `service/services/tools.py`
behind the Model Context Protocol so they can be:

  * Hosted independently of the agentic harness.
  * Discovered + called by any MCP-compatible client (the Coater 1
    advisor today; other Shaw AI projects tomorrow).
  * Versioned, observed, and governed independently.

There is **no logic duplication**. The handlers here are thin adapters
over the existing `TOOLS` registry — same code, same DB, same citations.

Run locally (stdio transport, default for MCP development):

    python -m mcp_server.server

Run as an HTTP/SSE server (production):

    python -m mcp_server.server --transport sse --host 0.0.0.0 --port 8765

See `docs/THREE_PLANE_ARCHITECTURE.md` §5 for the architectural rationale.
"""
