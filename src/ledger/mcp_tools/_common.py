"""Helpers shared by the MCP tool modules."""

import functools
import sys

from mcp.schemas.tools import MCPToolResult, MCPToolResultText


def ledger_path():
    """Return the current ledger path.

    Read live off ``ledger.mcp_server`` — not bound at import time — so
    tests can keep monkeypatching ``ledger.mcp_server.LEDGER_PATH`` and
    handlers pick up the replacement on every call.
    """
    from ledger.mcp_server import LEDGER_PATH

    return LEDGER_PATH


def _middleware() -> None:
    """Run the recurring-check middleware before a tool executes.

    Recurs immediately (via the hourly throttle in ``recurring_check``)
    when nothing is due. Settlements or errors are reported on stderr —
    never stdout, which carries the JSON-RPC protocol.
    """
    from ledger.recurring import recurring_check

    try:
        outcomes = recurring_check(ledger_path())
    except Exception as exc:
        sys.stderr.write(f"recurring check failed: {exc}\n")
        return
    for outcome in outcomes:
        rule_id = outcome["id"]
        if outcome["ok"]:
            sys.stderr.write(
                f"recurring: settled {rule_id} on {outcome.get('date', '')}\n"
            )
        else:
            for err in outcome.get("errors", []):
                sys.stderr.write(
                    f"recurring: {rule_id} not settled: "
                    f"{err.get('message') or err}\n"
                )


def with_middleware(fn):
    """Decorate a tool handler to run the recurring-check middleware first."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        _middleware()
        return await fn(*args, **kwargs)

    return wrapper


def _ok(text: str, structured: dict | None = None, isError: bool = False) -> MCPToolResult:
    """Build a text-backed MCPToolResult with optional structured payload."""
    return MCPToolResult(
        content=[MCPToolResultText(text=text)],
        structuredContent=structured,
        isError=isError,
    )


def _fail(what: str, exc: Exception) -> MCPToolResult:
    """Build an error MCPToolResult reporting a handler failure."""
    return MCPToolResult(
        content=[MCPToolResultText(text=f"{what} failed: {exc}")],
        isError=True,
    )