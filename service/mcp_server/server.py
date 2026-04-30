"""
MCP server exposing the deterministic tool layer.

Wraps `services.tools.TOOLS` — no logic duplication. Handlers here are
thin adapters that translate MCP `call_tool` into our existing
`call_tool(name, args)` and serialize the `ToolResult`.

The Model Context Protocol Python SDK (`pip install mcp`) is required
to run the server. It is intentionally **not** added to the service's
hard requirements yet — this scaffold exists to demonstrate the split
to IT, not to replace the in-process tool calls in the harness.

Transports:
  * stdio  — default, for local dev / IDE clients.
  * sse    — HTTP server-sent events, for hosted deployment.

Security note: this server is read-only by virtue of every tool in
`TOOLS` being read-only. There is no `write_*` tool, no DB-mutating
tool, and no file-system tool exposed here. The MCP transport adds no
new authority — it is a different way to call the same functions.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

# The MCP SDK is optional. We import lazily and print a clear install
# hint if it is missing, so this module is safe to import (and the rest
# of the codebase keeps working) even without the dep installed.
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
    _MCP_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when SDK missing
    _MCP_AVAILABLE = False
    Server = None  # type: ignore[assignment]
    stdio_server = None  # type: ignore[assignment]
    TextContent = None  # type: ignore[assignment]
    Tool = None  # type: ignore[assignment]


LOG = logging.getLogger("ignition_chatbot.mcp")

SERVER_NAME = "coater1-tools"
SERVER_VERSION = "0.1.0"


def _build_server() -> "Server":
    """Construct the MCP server and register every tool from `TOOLS`."""
    if not _MCP_AVAILABLE:
        raise RuntimeError(
            "The 'mcp' package is not installed. Install it with:\n"
            "    pip install mcp\n"
            "and re-run this server."
        )

    # Imported here so importing this module never triggers DB / model
    # initialization unless the server is actually being started.
    from services.tools import TOOLS, call_tool

    server: Server = Server(SERVER_NAME)

    @server.list_tools()  # type: ignore[misc]
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.parameters,
            )
            for spec in TOOLS.values()
        ]

    @server.call_tool()  # type: ignore[misc]
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        args = arguments or {}
        LOG.info("mcp call_tool name=%s args_keys=%s", name, list(args.keys()))
        result = await call_tool(name, args)
        # `to_llm_json` already produces the canonical JSON envelope
        # (ok / data / citation_id / error). MCP clients receive that
        # verbatim so behavior matches the in-process tool loop.
        return [TextContent(type="text", text=result.to_llm_json())]

    return server


async def _serve_stdio() -> None:
    server = _build_server()
    async with stdio_server() as (read_stream, write_stream):  # type: ignore[misc]
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


async def _serve_sse(host: str, port: int) -> None:
    """SSE transport for hosted deployment."""
    if not _MCP_AVAILABLE:
        raise RuntimeError("mcp package required for sse transport")
    try:
        from mcp.server.sse import SseServerTransport  # type: ignore
        from starlette.applications import Starlette
        from starlette.routing import Mount, Route
        import uvicorn
    except ImportError as e:
        raise RuntimeError(
            "SSE transport requires extras: pip install mcp[sse] starlette uvicorn"
        ) from e

    server = _build_server()
    sse = SseServerTransport("/messages")

    async def handle_sse(request):  # type: ignore[no-untyped-def]
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages", app=sse.handle_post_message),
        ]
    )
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    await uvicorn.Server(config).serve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Coater 1 MCP tool server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--list",
        action="store_true",
        help="List exposed tools as JSON and exit (no SDK required).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.list:
        # Dependency-free introspection so IT can see what the server
        # would expose without installing the MCP SDK first.
        from services.tools import TOOLS
        manifest = {
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "tools": [
                {
                    "name": s.name,
                    "description": s.description,
                    "input_schema": s.parameters,
                }
                for s in TOOLS.values()
            ],
        }
        print(json.dumps(manifest, indent=2))
        return 0

    if not _MCP_AVAILABLE:
        sys.stderr.write(
            "ERROR: the 'mcp' Python SDK is not installed.\n"
            "       Install with: pip install mcp\n"
            "       Or run with --list to inspect exposed tools without it.\n"
        )
        return 2

    if args.transport == "stdio":
        asyncio.run(_serve_stdio())
    else:
        asyncio.run(_serve_sse(args.host, args.port))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
