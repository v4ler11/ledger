"""Ledger MCP server: stdio and HTTP/SSE transports, and the tool registry.

Tool implementations live in :mod:`ledger.mcp_tools` (one module per tool
subset); this module wires them into an :class:`MCPServer` and owns the
JSON-RPC loops: a stdio loop (``mcp_stdio``) and an HTTP/SSE entry
(``mcp_server``, a FastAPI app mounting :class:`MCPRouter`). It re-exports
``LEDGER_TOOLS``, the handlers, and ``add_transaction_description`` for
existing importers.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

from mcp.schemas.other import (
    INTERNAL_ERROR,
    PARSE_ERROR,
    JsonRpcError,
    JsonRpcRequest,
)
from mcp.server import MCPServer

from ledger.globals import LEDGER_PATH
from ledger.mcp_tools import LEDGER_TOOLS
from ledger.mcp_tools.tool_accounts import handle_list_accounts
from ledger.mcp_tools.tool_transactions import (
    add_transaction_description,
    handle_add_transaction,
)

__all__ = [
    "LEDGER_PATH",
    "LEDGER_TOOLS",
    "handle_add_transaction",
    "handle_list_accounts",
    "add_transaction_description",
    "create_app",
    "create_server",
    "run_http",
    "run_stdio",
    "mcp_stdio",
    "mcp_server",
]


def create_server() -> MCPServer:
    """Build a ledger MCP server with the registered tools."""
    server = MCPServer("ledger")
    server.tools.add(list(LEDGER_TOOLS))
    return server


def create_app() -> FastAPI:
    """Build the FastAPI app exposing the ledger MCP server over HTTP/SSE.

    Mounts :class:`MCPRouter` at ``/mcp``: ``GET`` opens the SSE stream
    (returns an ``Mcp-Session-Id`` header), ``POST`` queues JSON-RPC
    requests for that session (202; replies arrive on the stream),
    ``DELETE`` closes it. Must be called from a running event loop —
    :class:`MCPRouter` spawns its session-cleanup task in ``__init__``.
    """
    from fastapi import FastAPI

    from mcp.router import MCPRouter

    app = FastAPI()
    app.include_router(MCPRouter(server=create_server()))
    return app


def _reply(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


async def _serve(server_factory) -> None:
    server = server_factory()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = JsonRpcRequest(**json.loads(line))
        except Exception as exc:
            _reply(JsonRpcError(code=PARSE_ERROR, message=str(exc)).model_dump())
            continue
        try:
            async for reply in server.process_request(request):
                if reply is not None:
                    _reply(reply.model_dump(exclude_none=True))
        except Exception as exc:
            _reply(
                JsonRpcError(
                    id=request.id, code=INTERNAL_ERROR, message=str(exc)
                ).model_dump()
            )


def run_stdio(server_factory=create_server) -> None:
    asyncio.run(_serve(server_factory))


def mcp_stdio():
    """Console-script entry point (``uv run mcp_stdio``)."""
    run_stdio(create_server)


async def run_http() -> None:
    """Serve the MCP HTTP/SSE server on ``LEDGER_MCP_HOST``/``LEDGER_MCP_PORT``.

    Runs in the calling event loop (no ``asyncio.run``) so it can be
    composed — ``uv run serve mcp`` runs it beside the Telegram bot. The
    FastAPI app is built inside the loop because :class:`MCPRouter`
    needs one at construction time. Serves ``/mcp`` (defaults
    ``127.0.0.1:8000``).
    """
    import uvicorn

    from ledger.globals import MCP_HOST, MCP_PORT

    app = create_app()
    config = uvicorn.Config(app, host=MCP_HOST, port=MCP_PORT)
    print(f"Serving at {MCP_HOST}:{MCP_PORT}")
    await uvicorn.Server(config).serve()


def mcp_server():
    """Console-script entry point (``uv run mcp_server``): HTTP/SSE server."""
    asyncio.run(run_http())


if __name__ == "__main__":
    mcp_server()


