"""Python library for Beancount v3 ledgers.

Pure Python bindings — no filesystem watcher.

  LedgerManager  — cached beanquery connection with mtime-based staleness
      checks and bean-check. Ledger files are not edited by hand while a
      program runs; the cache reloads lazily on change detection.
  transactions   — build and append Transaction objects to a ledger file.
  accounts       — open/close/modify account directives.
  commodities    — add/modify commodity directives.
  directives     — build and append the remaining directive types
      (balance, pad, note, document, price, event, query, custom).
  receipts       — structured purchase records archived in
      ``receipts/<YEAR>.jsonl`` (``Receipt`` model + ``add_receipt``).
  queries        — basic BQL query helpers (orientation, paging, tables,
      accounts, commodities, prices) returning pydantic models.
  tools          — chat-library bridge: the ledger MCP tools adapted to the
      chat ``Tool`` protocol; definitions stay in ``mcp_server``.
  mcp_server     — stdio MCP server exposing the write bindings as tools
      (imported lazily; run with ``python -m ledger.mcp_server``).
"""

from .ledger import (
    FileState,
    LedgerError,
    LedgerManager,
    _format_loader_error,
)
from . import (
    transactions,
    queries,
    models,
    commodities,
    accounts,
    directives,
    receipts,
    tools,
)
from .models import (
    AccountsList,
    Base,
    CheckResult,
    CommoditiesList,
    DateRange,
    LedgerInfo,
    LedgerIssue,
    Price,
    PricesList,
    QueryResult,
    TablesList,
)
from .accounts import (
    AccountEntry,
    add_account,
    append_accounts,
    close_account,
    format_account,
    make_close,
    make_open,
)
from .commodities import (
    add_commodity,
    append_commodities,
    format_commodity,
    make_commodity,
)
from .transactions import (
    PostingSpec,
    add_transaction,
    append_transactions,
    elided,
    format_transaction,
    leg,
    make_posting,
    make_transaction,
)
from .receipts import Receipt, ReceiptItem, add_receipt
from .directives import (
    add_balance,
    add_custom,
    add_document,
    add_event,
    add_note,
    add_pad,
    add_price,
    add_query,
    append_balances,
    append_customs,
    append_documents,
    append_events,
    append_notes,
    append_pads,
    append_prices,
    append_queries,
    format_balance,
    format_custom,
    format_document,
    format_event,
    format_note,
    format_pad,
    format_price,
    format_query,
    make_balance,
    make_custom,
    make_document,
    make_event,
    make_note,
    make_pad,
    make_price,
    make_query,
)
from .queries import (
    account_names,
    bean_check,
    commodity_names,
    date_range,
    ledger_info,
    list_accounts,
    list_commodities,
    list_prices,
    list_tables,
    run_query,
    table_names,
)
from .tools import (
    MCPTool,
    chat_tools,
    mcp_tool_to_chat_tool,
)
# ``ledger.mcp_server`` is imported lazily below: importing it at package
# import time makes runpy warn about ``python -m ledger.mcp_server`` ("found
# in sys.modules ... prior to execution ...").

_SERVER_NAMES = frozenset(
    {
        "mcp_server",
        "create_server",
        "handle_add_transaction",
        "handle_list_accounts",
        "run_stdio",
    }
)


def __getattr__(name: str):
    if name in _SERVER_NAMES:
        import importlib

        server = importlib.import_module(".mcp_server", __name__)
        if name == "mcp_server":
            return server
        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "FileState",
    "LedgerError",
    "LedgerManager",
    "PostingSpec",
    "AccountEntry",
    "AccountsList",
    "Base",
    "CheckResult",
    "CommoditiesList",
    "DateRange",
    "LedgerInfo",
    "LedgerIssue",
    "Price",
    "PricesList",
    "QueriesResult",
    "QueryResult",
    "Receipt",
    "ReceiptItem",
    "TablesList",
    "account_names",
    "add_account",
    "add_balance",
    "add_commodity",
    "add_custom",
    "add_document",
    "add_event",
    "add_note",
    "add_pad",
    "add_price",
    "add_query",
    "add_receipt",
    "add_transaction",
    "append_accounts",
    "append_balances",
    "append_commodities",
    "append_customs",
    "append_documents",
    "append_events",
    "append_notes",
    "append_pads",
    "append_prices",
    "append_queries",
    "append_transactions",
    "bean_check",
    "close_account",
    "commodity_names",
    "date_range",
    "format_account",
    "format_balance",
    "format_commodity",
    "format_custom",
    "format_document",
    "format_event",
    "format_note",
    "format_pad",
    "format_price",
    "format_query",
    "format_transaction",
    "ledger_info",
    "list_accounts",
    "list_commodities",
    "list_prices",
    "list_tables",
    "make_balance",
    "make_close",
    "make_commodity",
    "make_custom",
    "make_document",
    "make_event",
    "make_note",
    "make_open",
    "make_pad",
    "make_posting",
    "make_price",
    "make_query",
    "make_transaction",
    "elided",
    "leg",
    "models",
    "queries",
    "run_query",
    "table_names",
    "transactions",
    "directives",
    "mcp_server",
    "tools",
    "MCPTool",
    "chat_tools",
    "mcp_tool_to_chat_tool",
    "create_server",
    "handle_add_transaction",
    "handle_list_accounts",
    "run_stdio",
    "_format_loader_error",
]
