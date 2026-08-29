"""Chat-library bindings for the ledger MCP tools.

Tool definitions (names, descriptions, JSON schemas) and handlers are
single-sourced in :mod:`ledger.mcp_server`; this module bridges them to the
``chat`` library's ``Tool`` protocol so one description set drives both the
MCP server and an OpenAI-style chat loop.

Bridges:
  mcp_tool_to_chat_tool   MCP ``MCPToolDefinition``  -> chat ``ChatTool``
  tool_result_text        MCP ``MCPToolResult`` content -> plain text
  MCPTool                 chat ``Tool`` adapter that runs the MCP handler
  chat_tools              adapters for every tool registered in ``ledger.mcp_server``
"""

import asyncio
import collections.abc as abc
import json
from typing import List, Tuple

from chat.tools.abstract import Tool
from chat.tools.context import ToolContext
from chat.types import (
    ChatMessage,
    ChatMessageTool,
    ChatTool,
    ToolCall,
)
from mcp.schemas.tools import (
    MCPTool as MCPServerTool,
    MCPToolResult,
    MCPToolResultText,
)

__all__ = [
    "MCPTool",
    "chat_tools",
    "mcp_tool_to_chat_tool",
    "tool_result_text",
]


def tool_result_text(result: MCPToolResult) -> str:
    """Flatten an MCPToolResult's text content into a single string."""
    return "\n".join(
        part.text for part in result.content if isinstance(part, MCPToolResultText)
    )


def mcp_tool_to_chat_tool(tool: MCPServerTool) -> ChatTool:
    """Bridge an MCP tool definition to the chat library's ``ChatTool``.

    ``MCPToolDefinition.to_chat_tool_function()`` owns the conversion: it
    carries names, descriptions, and the required list verbatim from the MCP
    definition (the single source of truth), plus property metadata
    (type, description, format, enum) and nested ``items`` schemas
    (recursively, minus keywords the chat models don't carry, e.g.
    ``additionalProperties``/``minItems``). Array properties keep their
    ``items`` — providers like Google AI Studio reject arrays without it.
    """
    return ChatTool(function=tool.definition.to_chat_tool_function())


class MCPTool(Tool):
    """Adapt an MCP tool (definition + handler) to the chat ``Tool`` protocol.

    The handler and definition stay owned by ``ledger.mcp_server``; this adapter only
    changes the calling convention. It runs the handler the same way the MCP
    server does (including the tool's timeout), then turns the resulting
    ``MCPToolResult`` into a ``ChatMessageTool``. Chat has no separate error or
    structured-content channel, so a failed call's ``structuredContent`` is
    appended to the message text to keep errors visible to the model.
    """

    def __init__(self, tool: MCPServerTool):
        self.tool = tool

    @property
    def name(self) -> str:
        return self.tool.definition.name

    def into_chat_tool(self) -> ChatTool:
        return mcp_tool_to_chat_tool(self.tool)

    def validate_tool_call_args(
        self, ctx: ToolContext, tool_call: ToolCall, args: dict
    ) -> Tuple[bool, List[ChatMessage]]:
        # MCP handlers validate internally and report failures through the
        # MCPToolResult (see execute); there is no separate validation step.
        return True, []

    async def execute(
        self, ctx: ToolContext, tool_call: ToolCall, args: dict
    ) -> Tuple[bool, List[ChatMessage]]:
        try:
            result = await asyncio.wait_for(
                self._invoke(args), timeout=self.tool.timeout
            )
        except asyncio.TimeoutError:
            text = f"Tool '{self.name}' timed out ({self.tool.timeout}s)."
        except Exception as exc:
            text = f"Tool '{self.name}' failed: {exc}"
        else:
            text = tool_result_text(result)
            if result.isError and result.structuredContent:
                details = json.dumps(result.structuredContent)
                text = f"{text}\n{details}" if text else details
        return True, [
            ChatMessageTool(content=text, tool_call_id=tool_call.id)
        ]

    async def _invoke(self, args: dict) -> MCPToolResult:
        # MCP handlers are async functions returning an MCPToolResult, or
        # async generators streaming progress then the result (the server's
        # call convention). Run the handler the same way the server does:
        # await a coroutine, drain a stream for its terminal result.
        result = self.tool.func(args)
        if isinstance(result, abc.AsyncIterator):
            final: MCPToolResult | None = None
            async for part in result:
                if isinstance(part, MCPToolResult):
                    final = part
            if final is None:
                raise RuntimeError(f"tool '{self.name}' stream ended without a result")
            return final
        return await result


def chat_tools() -> List[Tool]:
    """The ledger's MCP tools adapted to the chat ``Tool`` protocol.

    Definitions are pulled from ``ledger.mcp_server`` at call time so importing this
    module never eagerly imports the server module (keeps ``python -m
    ledger.mcp_server`` warning-free); returned adapters share the handlers.
    """
    from .mcp_server import LEDGER_TOOLS  # lazy: keep the server module out of imports

    return [MCPTool(tool) for tool in LEDGER_TOOLS]
