"""MCP tools for ledger accounts: listing and opening accounts."""

from datetime import date

from mcp.schemas.tools import MCPTool, MCPToolDefinition, MCPToolResult

from ledger.accounts import add_account, format_account
from ledger.ledger import LedgerManager
from ledger.mcp_tools._common import _fail, _ok, ledger_path, with_middleware
from ledger.queries import list_accounts, list_balances


async def handle_list_accounts(args: dict) -> MCPToolResult:
    try:
        manager = LedgerManager(ledger_path())
        errors = manager.connection_errors()
        if errors:
            return _ok(
                "Ledger has loader errors; fix them before listing accounts.",
                {"accounts": [], "count": 0, "errors": errors},
                isError=True,
            )
        result = list_accounts(manager)
    except Exception as exc:
        return _fail("list_accounts", exc)

    return _ok(
        "\n".join(result.accounts) or "(no accounts declared)",
        {"accounts": result.accounts, "count": result.count},
    )


async def handle_list_balances(args: dict) -> MCPToolResult:
    try:
        manager = LedgerManager(ledger_path())
        errors = manager.connection_errors()
        if errors:
            return _ok(
                "Ledger has loader errors; fix them before listing balances.",
                {"balances": {}, "count": 0, "errors": errors},
                isError=True,
            )
        balances = list_balances(manager)
    except Exception as exc:
        return _fail("list_balances", exc)

    return _ok(
        "\n".join(
            f"{account}  {' | '.join(amounts)}"
            for account, amounts in balances.items()
        )
        or "(no accounts declared)",
        {"balances": balances, "count": len(balances)},
    )


async def handle_add_account(args: dict) -> MCPToolResult:
    try:
        open_, errors = add_account(
            ledger_path(),
            date.fromisoformat(args["date"]),
            args["account"],
            currencies=args.get("currencies"),
            booking=args.get("booking"),
        )
    except Exception as exc:
        return _fail("add_account", exc)

    return _ok(
        format_account(open_).rstrip(),
        {
            "date": open_.date.isoformat(),
            "account": open_.account,
            "currencies": sorted(open_.currencies) if open_.currencies else [],
            "booking": open_.booking.name if open_.booking else None,
            "errors": errors,
        },
        isError=bool(errors),
    )


add_account_description = """Open a new account in the ledger. Staged and validated before anything writes; on error nothing is written and errors are returned.

The directive is appended to the accounts split file (accounts.bean). Use a full Beancount account name (Root:Part, e.g. "Assets:Brokerage:Vanguard"). Call this before posting to a new account — posting to an unopened account fails validation.

Optional fields:
- currencies: restrict the account to these commodities, e.g. ["USD", "VTSAX"]. Omit for an unrestricted account (the default for cash accounts).
- booking: lot-booking method for tracked-lot accounts (brokerage, crypto, inventory), e.g. "FIFO". Omit for plain cash accounts.

Example:
  2024-05-01 open Assets:Brokerage:Vanguard
    currencies: "USD", "VTSAX"
    booking: "FIFO\""""


ACCOUNTS_TOOLS: tuple[MCPTool, ...] = (
    MCPTool(
        func=with_middleware(handle_add_account),
        definition=MCPToolDefinition(
            name="add_account",
            description=add_account_description,
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "format": "date",
                        "description": "ISO 8601 date the account is opened (YYYY-MM-DD), e.g. 2024-05-01.",
                    },
                    "account": {
                        "type": "string",
                        "description": "Full Beancount account name, e.g. 'Assets:Brokerage:Vanguard'. Root namespaces: Assets, Liabilities, Equity, Income, Expenses.",
                    },
                    "currencies": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Restrict the account to these commodities, e.g. [\"USD\", \"VTSAX\"]. Omit for an unrestricted account.",
                    },
                    "booking": {
                        "type": "string",
                        "enum": [
                            "STRICT",
                            "STRICT_WITH_SIZE",
                            "NONE",
                            "AVERAGE",
                            "FIFO",
                            "LIFO",
                            "HIFO",
                        ],
                        "description": "Lot-booking method for tracked-lot accounts (brokerage, crypto, inventory). Omit for plain cash accounts.",
                    },
                },
                "required": ["date", "account"],
            },
        ),
    ),
    MCPTool(
        func=with_middleware(handle_list_accounts),
        definition=MCPToolDefinition(
            name="list_accounts",
            description=(
                "List every account declared in the ledger, sorted "
                "alphabetically. Read-only; call it before posting "
                "transactions to confirm the exact account names — "
                "every account must be opened before it can be "
                "posted to."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ),
    MCPTool(
        func=with_middleware(handle_list_balances),
        definition=MCPToolDefinition(
            name="list_balances",
            description=(
                "Total balance per declared account: a dict of "
                "account -> list of summed amounts, one per currency "
                "(costed holdings appear as units, e.g. '10 AAPL', "
                "not converted value). Read-only; call it to see "
                "where money stands before posting transactions."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ),
)
