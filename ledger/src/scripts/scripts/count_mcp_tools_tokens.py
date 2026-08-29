"""Estimate the prompt-token cost of the registered MCP tools.

Heuristic (per tool): ``(description + compact-JSON input schema) // 4`` —
roughly one token per four characters. Rolled up per tool group (the
``tool_*.py`` modules), then one grand total.

Usage: ``uv run count_mcp_tools_tokens``
"""

from __future__ import annotations

import importlib
import json
import pkgutil

from tabulate import tabulate

from ledger import mcp_tools
from ledger.mcp_tools import LEDGER_TOOLS


def tool_groups() -> list[tuple[str, list]]:
    """Yield (group_label, [MCPTool, ...]) in registry order.

    Groups are the MCP tool modules (``tool_accounts`` etc.); a module's
    ``*_TOOLS`` tuples hold the tools of that subset. Together they are
    exactly ``LEDGER_TOOLS``, partitioned; groups and tools are ordered by
    their position in the registry.
    """
    groups: dict[str, list] = {}
    for modinfo in pkgutil.iter_modules(mcp_tools.__path__):
        if not modinfo.name.startswith("tool_"):
            continue
        mod = importlib.import_module(f"{mcp_tools.__name__}.{modinfo.name}")
        for attr in dir(mod):
            if attr.endswith("_TOOLS") and isinstance(getattr(mod, attr), tuple):
                groups[modinfo.name.removeprefix("tool_")] = list(getattr(mod, attr))
    position = {t.definition.name: i for i, t in enumerate(LEDGER_TOOLS)}
    grouped = {t.definition.name for tools in groups.values() for t in tools}
    assert grouped == set(position), "tool groups must cover the registry exactly"
    ordered = sorted(
        groups.items(), key=lambda kv: min(position[t.definition.name] for t in kv[1])
    )
    return [
        (label, sorted(tools, key=lambda t: position[t.definition.name]))
        for label, tools in ordered
    ]


def estimate_tokens(tool) -> int:
    """Estimated tokens consumed by one tool's description + schema."""
    definition = tool.definition
    body = definition.description + json.dumps(
        definition.inputSchema, separators=(",", ":")
    )
    return len(body) // 4


def main() -> None:
    groups = tool_groups()

    tool_rows: list[tuple[str, str, int, int]] = []
    group_rows: list[tuple[str, int, int, int]] = []
    total_tokens = 0
    for group_label, tools in groups:
        group_chars = 0
        for tool in tools:
            definition = tool.definition
            body = definition.description + json.dumps(
                definition.inputSchema, separators=(",", ":")
            )
            chars = len(body)
            tokens = chars // 4
            group_chars += chars
            tool_rows.append((definition.name, group_label, chars, tokens))
        group_rows.append(
            (
                group_label,
                len(tools),
                group_chars,
                sum(estimate_tokens(t) for t in tools),
            )
        )
        total_tokens += sum(estimate_tokens(t) for t in tools)
    total_chars = sum(row[2] for row in group_rows)

    print(
        tabulate(
            tool_rows,
            headers=["Tool", "Group", "Chars", "~Tokens"],
            tablefmt="github",
        )
    )
    print()
    print(
        tabulate(
            group_rows,
            headers=["Group", "Tools", "Chars", "~Tokens"],
            tablefmt="github",
        )
    )
    print()
    print(f"Total: ~{total_tokens} tokens ({total_chars} chars)")
    print("  (per tool: (description + stringified input schema) chars // 4)")


if __name__ == "__main__":
    main()