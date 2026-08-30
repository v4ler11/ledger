"""Tests for the ``uv run serve`` entry point (ledger.main)."""

import asyncio

import pytest

from ledger.main import parse_services, run


def test_parse_services_requires_at_least_one():
    with pytest.raises(SystemExit) as exc:
        parse_services([])
    assert exc.value.code == 2


def test_parse_services_single():
    assert parse_services(["mcp"]) == ["mcp"]
    assert parse_services(["tg"]) == ["tg"]


def test_parse_services_both_in_any_order():
    assert parse_services(["tg", "mcp"]) == ["tg", "mcp"]
    assert parse_services(["mcp", "tg"]) == ["mcp", "tg"]


def test_parse_services_rejects_unknown():
    with pytest.raises(SystemExit):
        parse_services(["http"])


def test_parse_services_dedupes():
    assert parse_services(["tg", "tg", "mcp"]) == ["tg", "mcp"]


def test_run_stops_other_when_first_service_finishes():
    """A graceful exit of one service cancels the one still running."""
    cancelled = asyncio.Event()

    async def long_running():
        try:
            await asyncio.sleep(3600)
        finally:
            cancelled.set()

    async def done_soon():
        return None

    async def scenario():
        await asyncio.wait_for(
            run(["tg", "mcp"], jobs={"tg": long_running(), "mcp": done_soon()}),
            timeout=10,
        )

    asyncio.run(scenario())
    assert cancelled.is_set()


def test_run_propagates_service_failure():
    """A failed service cancels the sibling and re-raises."""
    got_cancelled = asyncio.Event()

    async def long_running():
        try:
            await asyncio.sleep(3600)
        finally:
            got_cancelled.set()

    async def boom():
        raise RuntimeError("mcp died")

    with pytest.raises(RuntimeError, match="mcp died"):
        asyncio.run(
            asyncio.wait_for(
                run(["tg", "mcp"], jobs={"tg": long_running(), "mcp": boom()}),
                timeout=10,
            )
        )
    assert got_cancelled.is_set()
