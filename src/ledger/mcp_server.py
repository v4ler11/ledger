import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from mcp.schemas.other import (
    INTERNAL_ERROR,
    PARSE_ERROR,
    JsonRpcError,
    JsonRpcRequest,
)
from mcp.schemas.tools import MCPTool, MCPToolDefinition, MCPToolResult, MCPToolResultText
from mcp.server import MCPServer

from .accounts import add_account, format_account
from .transactions import add_transaction, format_transaction
from .receipts import Receipt, ReceiptItem, add_receipt
from .queries import list_accounts
from .ledger import LedgerManager


LEDGER_PATH = Path("/Users/valerii/code/finances/main.bean")


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


async def handle_list_accounts(args: dict) -> MCPToolResult:
    try:
        manager = LedgerManager(LEDGER_PATH)
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


def _posting_spec(spec: dict) -> dict:
    """Translate a JSON-Schema posting dict into an internal posting spec.

    The tool's public schema uses flat ``cost_*``/``price_*`` keys and an
    explicit ``elided`` flag; :func:`make_posting` consumes a nested
    ``cost``/``price`` dict and ``number=None`` for the elided balancing leg.
    """
    posting = {"account": spec["account"]}
    if spec.get("elided"):
        posting["number"] = None
    else:
        posting["number"] = spec["number"]
        posting["currency"] = spec["currency"]

    cost: dict = {}
    if "cost_number" in spec:
        cost["number"] = spec["cost_number"]
        cost["currency"] = spec["cost_currency"]
    if "cost_date" in spec:
        cost["date"] = date.fromisoformat(spec["cost_date"])
    if "cost_label" in spec:
        cost["label"] = spec["cost_label"]
    if cost:
        posting["cost"] = cost

    if "price_number" in spec:
        posting["price"] = {
            "number": spec["price_number"],
            "currency": spec["price_currency"],
        }

    if "flag" in spec:
        posting["flag"] = spec["flag"]
    if "meta" in spec:
        posting["meta"] = spec["meta"]
    return posting


async def handle_add_transaction(args: dict) -> MCPToolResult:
    # The "agent" tag lets every transaction written through the tool be
    # attributed back to the agent that requested it.
    tags = set(args.get("tags") or [])
    tags.add("agent")

    meta = dict(args.get("meta") or {})
    receipt_ids = args.get("receipt_ids")
    if receipt_ids:
        meta["receipt_ids"] = " ".join(receipt_ids)

    try:
        txn, errors = add_transaction(
            LEDGER_PATH,
            date.fromisoformat(args["date"]),
            args["narration"],
            [_posting_spec(p) for p in args["postings"]],
            payee=args.get("payee"),
            flag=args.get("flag", "*"),
            tags=tags,
            links=set(args["links"]) if args.get("links") else None,
            meta=meta,
        )
    except Exception as exc:
        return _fail("add_transaction", exc)

    return _ok(
        format_transaction(txn).rstrip(),
        {
            "date": txn.date.isoformat(),
            "narration": txn.narration,
            "payee": txn.payee,
            "flag": txn.flag,
            "postings": [p.account for p in txn.postings],
            "errors": errors,
        },
        isError=bool(errors),
    )


async def handle_add_account(args: dict) -> MCPToolResult:
    try:
        open_, errors = add_account(
            LEDGER_PATH,
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


async def handle_add_receipt(args: dict) -> MCPToolResult:
    try:
        receipt = Receipt(
            currency=args["currency"],
            store_name=args.get("store_name"),
            store_location=args.get("store_location"),
            items=[
                ReceiptItem(
                    name=item["name"],
                    name_inf=item.get("name_inf"),
                    qty=item.get("qty", 1.0),
                    amount=item["amount"],
                )
                for item in args["items"]
            ],
        )
        receipt_id = add_receipt(
            LEDGER_PATH,
            date.fromisoformat(args["date"]),
            receipt,
        )
    except Exception as exc:
        return _fail("add_receipt", exc)

    return _ok(
        f"Receipt added with id {receipt_id}",
        {
            "id": receipt_id,
            "date": args["date"],
            "store_name": args.get("store_name"),
            "store_location": args.get("store_location"),
        },
    )


add_account_description = """Open a new account in the ledger. Staged and validated before anything writes; on error nothing is written and errors are returned.

The directive is appended to the accounts split file (accounts.bean). Use a full Beancount account name (Root:Part, e.g. \"Assets:Brokerage:Vanguard\"). Call this before posting to a new account — posting to an unopened account fails validation.

Optional fields:
- currencies: restrict the account to these commodities, e.g. [\"USD\", \"VTSAX\"]. Omit for an unrestricted account (the default for cash accounts).
- booking: lot-booking method for tracked-lot accounts (brokerage, crypto, inventory), e.g. \"FIFO\". Omit for plain cash accounts.

Example:
  2024-05-01 open Assets:Brokerage:Vanguard
    currencies: \"USD\", \"VTSAX\"
    booking: \"FIFO\""""


add_transaction_description = """Append a validated Beancount transaction to the ledger. Staged and validated before anything writes; on error nothing is written and errors are returned.

Postings must sum to zero unless exactly one has elided=true (auto-computed to balance).

Examples:

1) Ordinary expense — no cost/price fields:
   Beancount:
     2024-02-01 * "Cafe" "Coffee"
       Expenses:Food:Coffee   3.50 EUR
       Assets:Checking
   Schema:
     postings: [
       {account: "Expenses:Food:Coffee", number: "3.50", currency: "EUR"},
       {account: "Assets:Checking", elided: true}
     ]

2) Buying a tracked lot — cost_* fields record per-unit acquisition cost:
   Beancount:
     2024-02-01 * "Broker" "Buy AAPL"
       Assets:Brokerage:AAPL   10 AAPL {150.00 USD, "lot-a"}
       Assets:Brokerage:Cash  -1500.00 USD
   Schema:
     postings: [
       {account: "Assets:Brokerage:AAPL", number: "10", currency: "AAPL", cost_number: "150.00", cost_currency: "USD", cost_label: "lot-a"},
       {account: "Assets:Brokerage:Cash", number: "-1500.00", currency: "USD"}
     ]

3) Currency conversion — price_* fields annotate FX value, no lot tracking:
   Beancount:
     2024-02-01 * "Transfer"
       Assets:EUR:Checking   -100.00 EUR @ 1.08 USD
       Assets:USD:Checking    108.00 USD
   Schema:
     postings: [
       {account: "Assets:EUR:Checking", number: "-100.00", currency: "EUR", price_number: "1.08", price_currency: "USD"},
       {account: "Assets:USD:Checking", number: "108.00", currency: "USD"}
     ]

Use cost_* only for tracked lots (stocks/crypto/inventory) you will later dispose of by lot. Use price_* only to annotate an FX rate on a plain conversion. Leave both blank for cash movements — the default case.

Link purchase evidence with receipt_ids: pass the receipt ids returned by the add_receipt tool (format YYYY-MM-DD-<hex>) — those ids are generated by add_receipt, never invented — and they are stored in the transaction meta as receipt_ids."""


add_receipt_description = """Archive a purchase receipt as one JSON record in receipts/<YEAR>.jsonl (year from the receipt date). Always returns a confirmation message with the generated receipt id (format YYYY-MM-DD-<hex>). The id is generated by the tool — do not pass one.

Use for purchase evidence: amount, merchant, and item lines. Capture what the receipt itself shows; keep item names short and store names consistent so receipts group by store."""


LEDGER_TOOLS: tuple[MCPTool, ...] = (
    MCPTool(
        func=handle_add_account,
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
        func=handle_add_transaction,
        definition=MCPToolDefinition(
            name="add_transaction",
            description=add_transaction_description,
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "format": "date",
                        "description": "ISO 8601 transaction date (YYYY-MM-DD), e.g. 2024-02-01.",
                    },
                    "narration": {
                        "type": "string",
                        "description": "Required human description shown on the directive row, e.g. \"Coffee at cafe\".",
                    },
                    "payee": {
                        "type": "string",
                        "description": "Optional merchant/counterparty displayed before the narration; keep it consistent so transactions group by who was paid.",
                    },
                    "flag": {
                        "type": "string",
                        "enum": ["*", "!"],
                        "description": "Transaction flag; '*' = cleared/normal (default), '!' = pending attention. Overridable per posting.",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags to attach (rendered as #tag); the tool always adds the agent tag.",
                    },
                    "links": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional links to related transactions (rendered as ^link).",
                    },
"meta": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Optional transaction-level metadata as Key: Value, e.g. {\"id\": \"seq-1\"}.",
                    },
                    "receipt_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Receipt IDs backing this transaction, as returned by the add_receipt tool (format YYYY-MM-DD-<hex>). Stored in the transaction meta as a space-separated receipt_ids string. Link every transaction to the receipt(s) that evidence it.",
                    },
                    "postings": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "account": {
                                    "type": "string",
                                    "description": "Full Beancount account name, e.g. 'Expenses:Food:Coffee' or 'Assets:Checking:BPI'.",
                                },
                                "elided": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": "Set true to auto-compute this posting's amount so the transaction balances. At most one posting per transaction may set this. When true, omit number and currency.",
                                },
                                "number": {
                                    "type": "string",
                                    "description": "Amount of units moved, as a decimal string (e.g. '12.50'). Positive or negative per normal Beancount sign convention. Required unless elided=true.",
                                },
                                "currency": {
                                    "type": "string",
                                    "description": "Commodity code for this posting's amount, e.g. 'EUR', 'USD', 'AAPL'. Required unless elided=true.",
                                },
                                "cost_number": {
                                    "type": "string",
                                    "description": "Per-unit acquisition cost, decimal string, e.g. '150.00'. Only for tracked-lot postings (stocks, crypto, inventory). Omit for ordinary cash postings.",
                                },
                                "cost_currency": {
                                    "type": "string",
                                    "description": "Currency the cost_number is denominated in, e.g. 'USD'. Required if cost_number is set.",
                                },
                                "cost_date": {
                                    "type": "string",
                                    "format": "date",
                                    "description": "Date the lot was originally acquired, if different from this transaction's date. Used to match which lot a later reducing posting draws down. Defaults to the transaction date if omitted.",
                                },
                                "cost_label": {
                                    "type": "string",
                                    "description": "Optional identifier to disambiguate this lot from others at the same cost and date.",
                                },
                                "price_number": {
                                    "type": "string",
                                    "description": "FX conversion rate for this posting's amount, decimal string. Not a tracked lot — just annotates what the amount was worth in another currency at transaction time.",
                                },
                                "price_currency": {
                                    "type": "string",
                                    "description": "Currency the price_number is denominated in. Required if price_number is set.",
                                },
                                "flag": {
                                    "type": "string",
                                    "enum": ["*", "!"],
                                    "description": "Overrides the transaction-level flag for this posting only.",
                                },
                                "meta": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                },
                            },
                            "required": ["account"],
                            "additionalProperties": False,
                        },
                        "description": "Every account and amount moved. Postings must balance to zero unless exactly one posting has elided=true. Validation fails closed: on any imbalance or malformed posting nothing is written and errors are returned. See tool description above for cost vs price examples.",
                    },
                },
                "required": ["date", "narration", "postings"],
            },
        ),
    ),
    MCPTool(
        func=handle_add_receipt,
        definition=MCPToolDefinition(
            name="add_receipt",
            description=add_receipt_description,
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "format": "date",
                        "description": "ISO 8601 receipt date (YYYY-MM-DD), e.g. 2026-08-21. Its year selects receipts/<YEAR>.jsonl.",
                    },
                    "currency": {
                        "type": "string",
                        "description": "Receipt currency code, e.g. 'EUR', 'USD'.",
                    },
                    "store_name": {
                        "type": "string",
                        "description": "Merchant/store name",
                    },
                    "store_location": {
                        "type": "string",
                        "description": "Optional store location (city, address).",
                    },
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "Verbatim OCR string from the receipt, never modified.",
                                },
                                "name_inf": {
                                    "type": "string",
                                    "description": "Guess at the real product name (may be wrong); omit if unknown.",
                                },
                                "qty": {
                                    "type": "number",
                                    "description": "Quantity of this line; defaults to 1.",
                                },
                                "amount": {
                                    "type": "number",
                                    "description": "Total price for this line in the receipt currency, e.g. 3.5.",
                                },
                            },
                            "required": ["name", "amount"],
                            "additionalProperties": False,
                        },
                        "description": "Item lines from the receipt, one per purchased line.",
                    },
                },
                "required": ["date", "currency", "items"],
            },
        ),
    ),
    MCPTool(
        func=handle_list_accounts,
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
)


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
