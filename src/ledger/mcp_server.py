"""Ledger MCP server: stdio transport and the tool registry.

Tool implementations live in :mod:`ledger.mcp_tools` (one module per tool
subset); this module wires them into an :class:`MCPServer` and owns the
JSON-RPC stdio loop. It re-exports ``LEDGER_TOOLS``, the handlers, and
``add_transaction_description`` for existing importers.
"""

import asyncio
import json
import sys

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
    "create_server",
    "run_stdio",
]


def create_server() -> MCPServer:
    """Build a ledger MCP server with the registered tools."""
    server = MCPServer("ledger")
    server.tools.add(list(LEDGER_TOOLS))
    return server


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


if __name__ == "__main__":
    run_stdio(create_server)