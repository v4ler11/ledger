"""Ledger service entry point (``uv run serve``).

Runs the Telegram bot (``tg``), the MCP HTTP/SSE server (``mcp``), or
both side by side on one event loop. At least one service is required.
With two, they share the loop: the first to finish (normal exit or
failure) cancels the other.

Examples::

    uv run serve tg
    uv run serve mcp
    uv run serve tg mcp
"""

import argparse
import asyncio
from collections.abc import Coroutine
from typing import Any

SERVICES = ("tg", "mcp")


def parse_services(argv: list[str] | None = None) -> list[str]:
    parser = argparse.ArgumentParser(
        prog="serve",
        description="Run the ledger Telegram bot (tg) and/or MCP server (mcp).",
    )
    parser.add_argument(
        "services",
        metavar="SERVICE",
        nargs="+",
        choices=SERVICES,
        help="services to run: tg, mcp (at least one)",
    )
    args = parser.parse_args(argv)
    return list(dict.fromkeys(args.services))


def build_jobs(services: list[str]) -> dict[str, Coroutine[Any, Any, None]]:
    jobs: dict[str, Coroutine[Any, Any, None]] = {}
    if "tg" in services:
        from tg_bot.tg_bot_server import main_async
        jobs["tg"] = main_async()
    if "mcp" in services:
        from ledger.mcp_server import run_http
        jobs["mcp"] = run_http()
    return jobs


async def run(
    services: list[str],
    jobs: dict[str, Coroutine[Any, Any, None]] | None = None,
) -> None:
    jobs = jobs or build_jobs(services)
    tasks = {name: asyncio.create_task(job) for name, job in jobs.items()}

    done, pending = await asyncio.wait(
        tasks.values(), return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    for task in done:
        if not task.cancelled() and (exc := task.exception()):
            raise exc


def main() -> None:
    asyncio.run(run(parse_services()))


if __name__ == "__main__":
    main()
