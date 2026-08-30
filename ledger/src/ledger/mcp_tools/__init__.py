"""MCP tool implementations, one module per tool subset.

Each ``tool_*.py`` owns its handlers, schemas, descriptions, and the
:class:`MCPTool` definitions for that subset. ``LEDGER_TOOLS`` assembles
the full registry in a stable order.
"""

from mcp.schemas.tools import MCPTool

from ledger.mcp_tools.tool_accounts import ACCOUNTS_TOOLS
from ledger.mcp_tools.tool_query import QUERY_TOOLS
from ledger.mcp_tools.tool_receipts import RECEIPT_TOOLS
from ledger.mcp_tools.tool_recurring import RECURRING_TOOLS
from ledger.mcp_tools.tool_transactions import TRANSACTION_TOOLS

LEDGER_TOOLS: tuple[MCPTool, ...] = (
    *ACCOUNTS_TOOLS,
    *QUERY_TOOLS,
    *TRANSACTION_TOOLS,
    *RECEIPT_TOOLS,
    *RECURRING_TOOLS,
)
