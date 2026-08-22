"""Tests for the chat-library bridge (ledger.tools)."""

import asyncio

from ledger import chat_tools
from ledger.mcp_server import LEDGER_TOOLS
from ledger.tools import MCPTool, mcp_tool_to_chat_tool, tool_result_text
from mcp.schemas.tools import MCPToolResult, MCPToolResultText
from chat.types import ChatMessageTool, ToolCall
from chat.tools.context import ToolContext


def tool_by_name(name: str):
    return next(t for t in LEDGER_TOOLS if t.definition.name == name)


def test_chat_tools_definitions_match_mcp():
    chat = chat_tools()
    assert [t.name for t in chat] == [
        t.definition.name for t in LEDGER_TOOLS
    ]
    # every registered MCP tool is exported to chat, and both sides share
    # the same names/descriptions: definitions are single-sourced in mcp.py
    mcp_by_name = {t.definition.name: t.definition for t in LEDGER_TOOLS}
    for tool in chat:
        definition = mcp_by_name[tool.name]
        assert tool.into_chat_tool().function.name == definition.name
        assert tool.into_chat_tool().function.description == definition.description


def test_mcp_tool_to_chat_tool_flat_schema():
    from ledger.mcp_server import add_transaction_description

    chat_tool = mcp_tool_to_chat_tool(tool_by_name("add_transaction"))
    func = chat_tool.function
    assert func.name == "add_transaction"
    assert func.description == add_transaction_description
    params = func.parameters
    assert params.type == "object"
    assert params.required == ["date", "narration", "postings"]
    # flat metadata is preserved; nested JSON-Schema keywords are dropped
    assert params.properties["date"].format == "date"
    assert params.properties["flag"].enum == ["*", "!"]
    posting = params.properties["postings"]
    assert posting.type == "array"
    # array items are preserved (Google AI Studio rejects arrays without
    # items); unsupported nested keywords are sanitized by the model
    assert posting.items.type == "object"
    assert posting.items.required == ["account"]
    assert posting.items.properties["account"].type == "string"
    assert not hasattr(posting.items, "additionalProperties")


def test_validate_does_not_gate_execution():
    """MCP handlers validate internally; the bridge defers to execution."""
    tool = MCPTool(tool_by_name("add_transaction"))
    ok, msgs = tool.validate_tool_call_args(None, None, {})
    assert ok is True
    assert msgs == []


def test_execute_add_transaction(scratch_ledger, monkeypatch):
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", scratch_ledger)
    tool = MCPTool(tool_by_name("add_transaction"))
    call = ToolCall(
        id="call_1",
        function={"name": "add_transaction", "arguments": "{}"},
    )
    args = {
        "date": "2024-03-01",
        "narration": "Coffee",
        "postings": [
            {"account": "Expenses:Food", "number": "8.50", "currency": "USD"},
            {"account": "Assets:Bank:Checking", "elided": True},
        ],
    }
    ctx = ToolContext(session=None)

    async def run():
        return await tool.execute(ctx, call, args)

    ok, msgs = asyncio.run(run())

    assert ok is True
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.role == "tool"
    assert msg.tool_call_id == "call_1"
    assert '2024-03-01 * "Coffee"' in msg.content
    assert "Failed" not in msg.content  # no error channel: failure is text-only
    assert '2024-03-01 * "Coffee"' in scratch_ledger.read_text()


def test_execute_reports_error_as_tool_message(scratch_ledger, monkeypatch):
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", scratch_ledger)
    tool = MCPTool(tool_by_name("add_transaction"))
    call = ToolCall(
        id="call_2",
        function={"name": "add_transaction", "arguments": "{}"},
    )
    args = {
        "date": "2024-03-01",
        "narration": "Bad",
        "postings": [
            {"account": "Assets:Nope", "number": "8.50", "currency": "USD"},
            {"account": "Assets:Cash", "elided": True},  # not opened → validation fails closed
        ],
    }
    ctx = ToolContext(session=None)

    async def run():
        return await tool.execute(ctx, call, args)

    ok, msgs = asyncio.run(run())

    assert ok is True
    assert len(msgs) == 1
    assert "Errors" in msgs[0].content or "error" in msgs[0].content.lower()
    assert "Bad" not in scratch_ledger.read_text()  # untouched on error


def test_execute_list_accounts(scratch_ledger, monkeypatch):
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", scratch_ledger)
    tool = MCPTool(tool_by_name("list_accounts"))
    call = ToolCall(
        id="call_3",
        function={"name": "list_accounts", "arguments": "{}"},
    )
    ctx = ToolContext(session=None)

    async def run():
        return await tool.execute(ctx, call, {})

    ok, msgs = asyncio.run(run())

    assert ok is True
    assert "Assets:Bank:Checking" in msgs[0].content


def test_tool_result_text_flattens_content():
    result = MCPToolResult(
        content=[
            MCPToolResultText(text="line one"),
            MCPToolResultText(text="line two"),
        ]
    )
    assert tool_result_text(result) == "line one\nline two"