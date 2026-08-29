"""Tests for the merged-chat mcp stdio server."""

import asyncio
import io
import json
import re

from ledger.mcp_server import create_server, run_stdio


def test_create_server_registers_add_transaction():
    async def build():
        return create_server()

    server = asyncio.run(build())
    tool = server.tools.get("add_transaction")
    assert tool is not None
    assert tool.definition.name == "add_transaction"
    assert set(tool.definition.inputSchema["required"]) == {
        "date",
        "narration",
        "postings",
    }


def test_create_server_registers_add_account():
    async def build():
        return create_server()

    server = asyncio.run(build())
    tool = server.tools.get("add_account")
    assert tool is not None
    assert tool.definition.name == "add_account"
    assert set(tool.definition.inputSchema["required"]) == {"date", "account"}
    assert tool.definition.inputSchema["properties"]["booking"]["enum"]


def test_run_stdio_appends_transaction(scratch_ledger, monkeypatch):
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", scratch_ledger)
    call = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "add_transaction",
            "arguments": {
                "date": "2024-03-01",
                "narration": "Coffee",
"postings": [
                    {"account": "Expenses:Food", "number": "8.50", "currency": "USD"},
                    {"account": "Assets:Bank:Checking", "elided": True},
                ],
            },
        },
    }
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call) + "\n"))
    monkeypatch.setattr("sys.stdout", stdout)

    run_stdio(create_server)

    lines = [json.loads(line) for line in stdout.getvalue().strip().splitlines()]
    assert len(lines) == 1
    result = lines[0]["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["errors"] == []
    assert '2024-03-01 * "Coffee"' in scratch_ledger.read_text()


def test_run_stdio_rich_posting_dict(scratch_ledger, monkeypatch):
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", scratch_ledger)
    call = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "add_transaction",
            "arguments": {
                "date": "2024-04-02",
                "narration": "Buy AAPL",
                "postings": [
                    {
                        "account": "Assets:Broker",
                        "number": "5",
                        "currency": "AAPL",
                        "cost_number": "182.00",
                        "cost_currency": "USD",
                        "price_number": "197.90",
                        "price_currency": "USD",
                    },
                    {"account": "Assets:Bank:Checking",
                     "number": "-910.00", "currency": "USD"},
                ],
            },
        },
    }
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call) + "\n"))
    monkeypatch.setattr("sys.stdout", stdout)

    run_stdio(create_server)

    result = json.loads(stdout.getvalue().strip().splitlines()[0])["result"]
    assert result["isError"] is False
    text = scratch_ledger.read_text()
    assert "5 AAPL {182.00 USD} @ 197.90 USD" in text
    assert "-910.00 USD" in text


def test_run_stdio_adds_account(scratch_ledger, monkeypatch):
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", scratch_ledger)
    call = {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {
            "name": "add_account",
            "arguments": {
                "date": "2024-06-01",
                "account": "Assets:Brokerage:Vanguard",
                "currencies": ["USD", "VTSAX"],
                "booking": "FIFO",
            },
        },
    }
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call) + "\n"))
    monkeypatch.setattr("sys.stdout", stdout)

    run_stdio(create_server)

    result = json.loads(stdout.getvalue().strip().splitlines()[0])["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["errors"] == []
    assert result["structuredContent"]["date"] == "2024-06-01"
    assert result["structuredContent"]["account"] == "Assets:Brokerage:Vanguard"
    assert result["structuredContent"]["currencies"] == ["USD", "VTSAX"]
    assert result["structuredContent"]["booking"] == "FIFO"
    # routed to the accounts split file, not the main ledger
    text = (scratch_ledger.parent / "accounts.bean").read_text()
    assert "open Assets:Brokerage:Vanguard" in text
    assert "VTSAX" in text
    assert "FIFO" in text


def test_run_stdio_adds_account_fails_closed_on_duplicate(tmp_path, monkeypatch):
    """Re-opening an already-open account is rejected; ledger untouched."""
    ledger = tmp_path / "b.bean"
    ledger.write_text(
        'option "operating_currency" "USD"\n'
        "1970-01-01 open Assets:Cash\n"
    )
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", ledger)
    call = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {
            "name": "add_account",
            "arguments": {
                "date": "2024-03-01",
                "account": "Assets:Cash",
            },
        },
    }
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call) + "\n"))
    monkeypatch.setattr("sys.stdout", stdout)

    run_stdio(create_server)

    result = json.loads(stdout.getvalue().strip().splitlines()[0])["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["errors"]
    assert ledger.read_text().count("open Assets:Cash") == 1  # untouched


def test_run_stdio_reports_breaking_transaction(tmp_path, monkeypatch):
    ledger = tmp_path / "b.bean"
    ledger.write_text(
        'option "operating_currency" "USD"\n'
        "1970-01-01 open Assets:Cash\n"
        "1970-01-01 open Expenses:Food\n"
    )
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", ledger)
    call = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "add_transaction",
            "arguments": {
                "date": "2024-03-01",
                "narration": "Bad",
                "postings": [
                    {"account": "Assets:Nope", "number": "8.50", "currency": "USD"},
                    {"account": "Assets:Cash", "elided": True},
                ],
            },
        },
    }
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call) + "\n"))
    monkeypatch.setattr("sys.stdout", stdout)

    run_stdio(create_server)

    result = json.loads(stdout.getvalue().strip().splitlines()[0])["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["errors"]
    assert '2024-03-01 * "Bad"' not in ledger.read_text()  # untouched on error


def test_create_server_registers_add_receipt():
    async def build():
        return create_server()

    server = asyncio.run(build())
    tool = server.tools.get("add_receipt")
    assert tool is not None
    assert tool.definition.name == "add_receipt"
    assert set(tool.definition.inputSchema["required"]) == {"date", "currency", "items"}


def test_run_stdio_receipt_ids_land_in_meta(scratch_ledger, monkeypatch):
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", scratch_ledger)
    call = {
        "jsonrpc": "2.0",
        "id": 12,
        "method": "tools/call",
        "params": {
            "name": "add_transaction",
            "arguments": {
                "date": "2026-08-21",
                "narration": "Coffee + cake",
                "receipt_ids": ["2026-08-21-53f7acbb"],
                "postings": [
                    {"account": "Expenses:Food", "number": "9.50", "currency": "EUR"},
                    {"account": "Assets:Cash", "elided": True},
                ],
            },
        },
    }
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call) + "\n"))
    monkeypatch.setattr("sys.stdout", stdout)

    run_stdio(create_server)

    result = json.loads(stdout.getvalue().strip().splitlines()[0])["result"]
    assert result["isError"] is False
    text = scratch_ledger.read_text()
    assert 'receipt_ids: "2026-08-21-53f7acbb"' in text
    assert "53f7acbb" in text


def test_run_stdio_receipt_ids_missing_meta_untouched(scratch_ledger, monkeypatch):
    """No receipt_ids arg -> no receipt_ids meta key."""
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", scratch_ledger)
    call = {
        "jsonrpc": "2.0",
        "id": 13,
        "method": "tools/call",
        "params": {
            "name": "add_transaction",
            "arguments": {
                "date": "2026-03-22",
                "narration": "No receipt",
                "postings": [
                    {"account": "Expenses:Food", "number": "2.00", "currency": "EUR"},
                    {"account": "Assets:Cash", "elided": True},
                ],
            },
        },
    }
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call) + "\n"))
    monkeypatch.setattr("sys.stdout", stdout)

    run_stdio(create_server)

    result = json.loads(stdout.getvalue().strip().splitlines()[0])["result"]
    assert result["isError"] is False
    assert "receipt_ids" not in scratch_ledger.read_text()


def test_run_stdio_receipt_ids_multiple_space_joined(scratch_ledger, monkeypatch):
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", scratch_ledger)
    call = {
        "jsonrpc": "2.0",
        "id": 14,
        "method": "tools/call",
        "params": {
            "name": "add_transaction",
            "arguments": {
                "date": "2026-08-21",
                "narration": "Two receipts",
                "receipt_ids": ["2026-08-21-53f7acbb", "2026-08-21-0011aabb"],
                "postings": [
                    {"account": "Expenses:Food", "number": "9.50", "currency": "EUR"},
                    {"account": "Assets:Cash", "elided": True},
                ],
            },
        },
    }
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call) + "\n"))
    monkeypatch.setattr("sys.stdout", stdout)

    run_stdio(create_server)

    result = json.loads(stdout.getvalue().strip().splitlines()[0])["result"]
    assert result["isError"] is False
    assert (
        'receipt_ids: "2026-08-21-53f7acbb 2026-08-21-0011aabb"' in scratch_ledger.read_text()
    )


def test_run_stdio_adds_receipt(scratch_ledger, monkeypatch):
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", scratch_ledger)
    call = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {
            "name": "add_receipt",
            "arguments": {
                "date": "2026-08-21",
                "currency": "EUR",
                "store_name": "Cafe Gato",
                "store_location": "Berlin",
"items": [
                    {"name": "CAPPUCINO", "name_inf": "cappuccino", "unit": "pcs", "amount": 3.5},
                    {"name": "TIRAMISU", "amount": 6.0},
                ],
            },
        },
    }
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call) + "\n"))
    monkeypatch.setattr("sys.stdout", stdout)

    run_stdio(create_server)

    result = json.loads(stdout.getvalue().strip().splitlines()[0])["result"]
    assert result["isError"] is False
    assert "Receipt added with id " in result["content"][0]["text"]
    receipt_id = result["structuredContent"]["id"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-[0-9a-f]{8}", receipt_id)
    assert result["structuredContent"]["date"] == "2026-08-21"
    assert result["structuredContent"]["store_name"] == "Cafe Gato"
    # one JSON record per line in the yearly archive, id matches the reply
    archive = scratch_ledger.parent / "receipts" / "2026.jsonl"
    lines = archive.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    stored = json.loads(lines[0])
    assert stored["id"] == receipt_id
    assert stored["date"] == "2026-08-21"
    assert stored["currency"] == "EUR"
    assert stored["items"] == [
        {"name": "CAPPUCINO", "name_inf": "cappuccino", "qty": 1.0, "unit": "pcs", "amount": 3.5},
        {"name": "TIRAMISU", "name_inf": None, "qty": 1.0, "unit": "pcs", "amount": 6.0},
    ]


def test_run_stdio_add_receipt_fails_on_missing_required(tmp_path, monkeypatch):
    ledger = tmp_path / "b.bean"
    ledger.write_text('option "operating_currency" "USD"\n')
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", ledger)
    call = {
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {
            "name": "add_receipt",
            "arguments": {"date": "2026-08-21"},  # currency + items missing
        },
    }
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call) + "\n"))
    monkeypatch.setattr("sys.stdout", stdout)

    run_stdio(create_server)

    result = json.loads(stdout.getvalue().strip().splitlines()[0])["result"]
    assert result["isError"] is True
    assert "add_receipt failed" in result["content"][0]["text"]
    assert not (tmp_path / "receipts").exists()  # nothing written


def test_run_stdio_lists_accounts(scratch_ledger, monkeypatch):
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", scratch_ledger)
    call = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {
            "name": "list_accounts",
            "arguments": {},
        },
    }
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call) + "\n"))
    monkeypatch.setattr("sys.stdout", stdout)

    run_stdio(create_server)

    result = json.loads(stdout.getvalue().strip().splitlines()[0])["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["accounts"] == [
        "Assets:Bank:Checking",
        "Assets:Broker",
        "Assets:Cash",
        "Equity:Opening-Balances",
        "Expenses:Food",
        "Expenses:Home",
        "Income:Salary",
    ]
    assert result["structuredContent"]["count"] == 7


def test_run_stdio_lists_balances(scratch_ledger, monkeypatch):
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", scratch_ledger)
    call = {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {
            "name": "list_balances",
            "arguments": {},
        },
    }
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call) + "\n"))
    monkeypatch.setattr("sys.stdout", stdout)

    run_stdio(create_server)

    result = json.loads(stdout.getvalue().strip().splitlines()[0])["result"]
    assert result["isError"] is False
    balances = result["structuredContent"]["balances"]
    assert balances["Assets:Cash"] == ["1000.00 USD"]
    assert balances["Assets:Broker"] == ["10 AAPL"]
    assert result["structuredContent"]["count"] == 7


def test_run_stdio_lists_accounts_fails_closed_on_broken_ledger(tmp_path, monkeypatch):
    """A ledger with loader errors returns them instead of a partial list."""
    ledger = tmp_path / "b.bean"
    ledger.write_text(
        'option "operating_currency" "USD"\n'
        "2024-03-01 * \"Broken\"\n"  # posting to an unopened account
        "  Assets:Nope  8.50 USD\n"
        "  Assets:Nope  -8.50 USD\n"
    )
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", ledger)
    call = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "list_accounts",
            "arguments": {},
        },
    }
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call) + "\n"))
    monkeypatch.setattr("sys.stdout", stdout)

    run_stdio(create_server)

    result = json.loads(stdout.getvalue().strip().splitlines()[0])["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["accounts"] == []
    assert result["structuredContent"]["errors"]
