"""MCP tools for recurring payment rules."""

from datetime import date

from mcp.schemas.tools import MCPTool, MCPToolDefinition, MCPToolResult

from ledger.mcp_tools._common import _fail, _ok, ledger_path, with_middleware
from ledger.recurring import (
    RecurringRule,
    add_rule,
    delete_rule,
    format_rule,
    list_rules,
    update_rule,
)


async def handle_recurring_list(args: dict) -> MCPToolResult:
    try:
        rules = list_rules(ledger_path())
    except Exception as exc:
        return _fail("list_recurring", exc)

    def view(rule: RecurringRule) -> dict:
        return {
            "id": rule.id,
            "payee": rule.payee,
            "narration": rule.narration,
            "account_from": rule.account_from,
            "account_to": rule.account_to,
            "amount": rule.amount,
            "currency": rule.currency,
            "rrule": rule.rrule,
            "next_settle_date": rule.next_settle_date.isoformat(),
            "status": rule.status,
        }

    return _ok(
        "\n".join(format_rule(r) for r in rules) or "(no recurring rules)",
        {"rules": [view(r) for r in rules], "count": len(rules)},
    )


async def handle_recurring_add(args: dict) -> MCPToolResult:
    try:
        rule = add_rule(
            ledger_path(),
            RecurringRule(
                id=args["id"],
                payee=args["payee"],
                narration=args.get("narration", ""),
                account_from=args["account_from"],
                account_to=args["account_to"],
                amount=args["amount"],
                currency=args["currency"],
                rrule=args["rrule"],
                next_settle_date=date.fromisoformat(args["next_settle_date"]),
                status=args.get("status", "active"),
            ),
        )
    except Exception as exc:
        return _fail("add_recurring", exc)
    return _ok(
        f"Added recurring rule {rule.id}: {format_rule(rule)}",
        {"id": rule.id, "next_settle_date": rule.next_settle_date.isoformat()},
    )


async def handle_recurring_update(args: dict) -> MCPToolResult:
    changes = dict(args.get("changes") or {})
    if "next_settle_date" in changes:
        changes["next_settle_date"] = date.fromisoformat(changes["next_settle_date"])
    if "amount" in changes:
        changes["amount"] = float(changes["amount"])
    try:
        rule = update_rule(ledger_path(), args["id"], **changes)
    except Exception as exc:
        return _fail("update_recurring", exc)
    return _ok(
        f"Updated recurring rule {rule.id}: {format_rule(rule)}",
        {"id": rule.id, "next_settle_date": rule.next_settle_date.isoformat()},
    )


async def handle_recurring_delete(args: dict) -> MCPToolResult:
    try:
        removed = delete_rule(ledger_path(), args["id"])
    except Exception as exc:
        return _fail("delete_recurring", exc)
    return _ok(
        f"Deleted recurring rule {removed.id}",
        {"id": removed.id},
    )


recurring_rule_schema = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "Stable identifier for this rule, e.g. 'kagi-search'. Stored as the transaction meta recurring-id when settled.",
        },
        "payee": {
            "type": "string",
            "description": "Counterparty/merchant name, e.g. 'Kagi'.",
        },
        "narration": {
            "type": "string",
            "description": "Transaction narration, e.g. 'Monthly search subscription'. Defaults to payee when omitted.",
        },
        "account_from": {
            "type": "string",
            "description": "Funding/source account, e.g. 'Assets:Wise:USD'. This is the elided balancing leg when settled.",
        },
        "account_to": {
            "type": "string",
            "description": "Expense/income account, e.g. 'Expenses:Software'.",
        },
        "amount": {
            "type": "number",
            "description": "Amount moved each period, e.g. 12.3.",
        },
        "currency": {
            "type": "string",
            "description": "Currency code, e.g. 'EUR', 'USD'.",
        },
        "rrule": {
            "type": "string",
            "description": "RFC5545 recurrence, e.g. 'FREQ=MONTHLY;BYMONTHDAY=5'.",
        },
        "next_settle_date": {
            "type": "string",
            "format": "date",
            "description": "ISO date the next payment is due (YYYY-MM-DD). Settled lazily when today is past this date.",
        },
        "status": {
            "type": "string",
            "enum": ["active", "paused"],
            "description": "Settlement state; paused rules are kept but not settled. Defaults to 'active'.",
        },
    },
    "required": [
        "id", "payee", "account_from", "account_to",
        "amount", "currency", "rrule", "next_settle_date",
    ],
    "additionalProperties": False,
}


list_recurring_description = """List every recurring payment rule sorted by id. Read-only; confirms scheduled rules and their next settle dates before editing or awaiting settlement."""


add_recurring_description = """Add a recurring payment rule to recurring.json (next to the ledger, beside accounts.bean). The rule is settled lazily on its next_settle_date, advancing to the next rrule occurrence after each payment. The id must be unique; an existing id is rejected. The rule is written only after the ledger validates it, and on error nothing is written."""


update_recurring_description = """Update fields of an existing recurring rule. Pass a changes object with the subset of fields to change (status, amount, currency, rrule, next_settle_date, payee, narration, account_from, account_to). next_settle_date is parsed as a date and amount as a number. Fails if the id does not exist."""


delete_recurring_description = """Delete a recurring payment rule by id. Fails if the id does not exist."""


RECURRING_TOOLS: tuple[MCPTool, ...] = (
    MCPTool(
        func=with_middleware(handle_recurring_list),
        definition=MCPToolDefinition(
            name="list_recurring",
            description=list_recurring_description,
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ),
    MCPTool(
        func=with_middleware(handle_recurring_add),
        definition=MCPToolDefinition(
            name="add_recurring",
            description=add_recurring_description,
            inputSchema={
                "type": "object",
                "properties": {**recurring_rule_schema["properties"]},
                "required": recurring_rule_schema["required"],
                "additionalProperties": False,
            },
        ),
    ),
    MCPTool(
        func=with_middleware(handle_recurring_update),
        definition=MCPToolDefinition(
            name="update_recurring",
            description=update_recurring_description,
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Rule id to update."},
                    "changes": {
                        "type": "object",
                        "properties": {
                            "payee": {"type": "string"},
                            "narration": {"type": "string"},
                            "account_from": {"type": "string"},
                            "account_to": {"type": "string"},
                            "amount": {"type": "number"},
                            "currency": {"type": "string"},
                            "rrule": {"type": "string"},
                            "next_settle_date": {
                                "type": "string",
                                "format": "date",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["active", "paused"],
                            },
                        },
                        "additionalProperties": False,
                    },
                },
                "required": ["id", "changes"],
                "additionalProperties": False,
            },
        ),
    ),
    MCPTool(
        func=with_middleware(handle_recurring_delete),
        definition=MCPToolDefinition(
            name="delete_recurring",
            description=delete_recurring_description,
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Rule id to delete."},
                },
                "required": ["id"],
                "additionalProperties": False,
            },
        ),
    ),
)
