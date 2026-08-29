"""Tests for the recurring payment store and lazy settlement."""

from datetime import date
from typing import Any, Dict

import pytest

from ledger.recurring import (
    RecurringRule,
    add_rule,
    delete_rule,
    format_rule,
    list_rules,
    load_rules,
    process_recurring,
    store_path,
    update_rule,
)


@pytest.fixture
def rules_ledger(scratch_ledger, monkeypatch):
    """Scratch ledger with the sample accounts and a clean recurring store."""
    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", scratch_ledger)
    return scratch_ledger


def make_rule(**overrides: Any):
    fields: Dict[str, Any] = dict(
        id="kagi-search",
        payee="Kagi",
        narration="Monthly search subscription",
        account_from="Assets:Bank:Checking",
        account_to="Expenses:Food",
        amount=12.30,
        currency="USD",
        rrule="FREQ=MONTHLY;BYMONTHDAY=5",
        next_settle_date=date(2026, 9, 5),
    )
    fields.update(overrides)
    return RecurringRule(**fields)


def test_store_path_next_to_ledger(tmp_path):
    ledger = tmp_path / "main.bean"
    assert store_path(ledger) == tmp_path / "recurring.json"


def test_add_update_delete_list_roundtrip(scratch_ledger):
    assert list_rules(scratch_ledger) == []
    add_rule(scratch_ledger, make_rule())
    add_rule(scratch_ledger, make_rule(id="second", amount=5.0))

    listed = list_rules(scratch_ledger)
    assert [r.id for r in listed] == ["kagi-search", "second"]

    # file is saved with indent=4
    raw = store_path(scratch_ledger).read_text()
    assert raw.count("\n    ") > 0

    updated = update_rule(scratch_ledger, "second", status="paused")
    assert updated.status == "paused"

    removed = delete_rule(scratch_ledger, "second")
    assert removed.id == "second"
    assert [r.id for r in list_rules(scratch_ledger)] == ["kagi-search"]


def test_add_duplicate_id_rejected(scratch_ledger):
    add_rule(scratch_ledger, make_rule())
    with pytest.raises(ValueError):
        add_rule(scratch_ledger, make_rule())


def test_update_missing_id_raises(scratch_ledger):
    with pytest.raises(KeyError):
        update_rule(scratch_ledger, "nope", payee="X")


def test_delete_missing_id_raises(scratch_ledger):
    with pytest.raises(KeyError):
        delete_rule(scratch_ledger, "nope")


def test_persisted_store_reloads(scratch_ledger):
    add_rule(scratch_ledger, make_rule())
    # fresh disk read round-trips the parsed rule
    store = load_rules(scratch_ledger)
    rule = store.rules["kagi-search"]
    assert rule.next_settle_date == date(2026, 9, 5)
    assert rule.amount == 12.30


def test_format_rule_renders_fields(scratch_ledger):
    rule = make_rule()
    text = format_rule(rule)
    assert "Monthly search subscription" in text
    assert "12.3" in text  # trailing zeros trimmed
    assert "#kagi-search" in text
    assert "Assets:Bank:Checking -> Expenses:Food" in text


def test_process_recurring_settles_due_rule(scratch_ledger):
    add_rule(scratch_ledger, make_rule())  # due 2026-09-05
    outcomes = process_recurring(scratch_ledger, today=date(2026, 9, 6))

    assert outcomes == [
        {
            "id": "kagi-search",
            "ok": True,
            "date": "2026-09-06",
            "next": "2026-10-05",
        }
    ]
    # the transaction is written dated on the due date with the id meta
    text = scratch_ledger.read_text()
    assert "2026-09-05 * " in text
    assert "recurring-id: \"kagi-search\"" in text
    assert "Expenses:Food" in text
    # and the rule advanced for next month
    rule = load_rules(scratch_ledger).rules["kagi-search"]
    assert rule.next_settle_date == date(2026, 10, 5)


def test_process_recurring_settles_rule_due_today(scratch_ledger):
    """A rule due on the calendar day matching ``today`` settles today.

    The due day is compared against end-of-day today (23:59:59); a midnight
    ``today`` would compare equal and skip it, so a rule landing on e.g. the
    31st would never be settled on that day.
    """
    add_rule(scratch_ledger, make_rule(next_settle_date=date(2026, 9, 5)))
    outcomes = process_recurring(scratch_ledger, today=date(2026, 9, 5))

    assert outcomes[0]["ok"] is True
    # transactions dated on the due date, not deferred a day
    assert "2026-09-05 * " in scratch_ledger.read_text()
    rule = load_rules(scratch_ledger).rules["kagi-search"]
    assert rule.next_settle_date == date(2026, 10, 5)


def test_process_recurring_skips_future_and_paused(scratch_ledger):
    add_rule(scratch_ledger, make_rule(next_settle_date=date(2026, 10, 5)))  # future
    add_rule(
        scratch_ledger,
        make_rule(id="paused", next_settle_date=date(2026, 1, 1), status="paused"),
    )
    outcomes = process_recurring(scratch_ledger, today=date(2026, 9, 6))
    assert outcomes == []
    assert "2026-10-05" not in scratch_ledger.read_text()  # nothing written


def test_process_recurring_sets_failed_rule_not_rolled_forward(scratch_ledger):
    # points at an account that does not exist -> ledger validation fails
    add_rule(
        scratch_ledger,
        make_rule(id="bad", account_to="Expenses:Unknown:Nope", next_settle_date=date(2026, 1, 1)),
    )
    outcomes = process_recurring(scratch_ledger, today=date(2026, 9, 6))
    assert len(outcomes) == 1
    assert outcomes[0]["ok"] is False
    assert outcomes[0].get("errors")
    # not advanced, so a later check retries
    assert load_rules(scratch_ledger).rules["bad"].next_settle_date == date(2026, 1, 1)


def test_recurring_posts_elided_from_source(scratch_ledger):
    """The source account is the elided balancing leg, so it must balance."""
    add_rule(scratch_ledger, make_rule(next_settle_date=date(2026, 9, 5)))
    process_recurring(scratch_ledger, today=date(2026, 9, 6))
    # direct re-validation shows a clean ledger (balances after settlement)
    from ledger.ledger import LedgerManager

    errors = LedgerManager(scratch_ledger).connection_errors()
    assert errors == []


# ---------------------------------------------------------------------------
# MCP tool integration: the 4 recurring tools + the middleware sweep
# ---------------------------------------------------------------------------


def _call(server, name, arguments) -> dict:
    import asyncio
    import json
    import sys
    import io
    from typing import cast

    from mcp.schemas.other import JsonRpcRequest

    request = JsonRpcRequest(jsonrpc="2.0", id=1, method="tools/call",
                             params={"name": name, "arguments": arguments})
    out = io.StringIO()
    sys.stdout = out

    async def run() -> dict:
        async for reply in server.process_request(request):
            if reply is not None:
                return cast(dict, reply.model_dump(exclude_none=True))
        # The server always replies to tools/call; a missing reply fails the test.
        raise AssertionError("server produced no reply")

    try:
        return asyncio.run(run())
    finally:
        sys.stdout = sys.__stdout__


def test_mcp_add_update_delete_list(scratch_ledger, monkeypatch):
    from ledger.mcp_server import create_server

    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", scratch_ledger)
    server = create_server()

    rule = {
        "id": "kagi-search",
        "payee": "Kagi",
        "narration": "Monthly search subscription",
        "account_from": "Assets:Bank:Checking",
        "account_to": "Expenses:Food",
        "amount": 12.30,
        "currency": "USD",
        "rrule": "FREQ=MONTHLY;BYMONTHDAY=5",
        "next_settle_date": "2026-09-05",
    }

    added = _call(server, "add_recurring", rule)
    assert added["result"]["isError"] is False
    assert added["result"]["structuredContent"]["id"] == "kagi-search"

    # duplicate id is rejected
    dup = _call(server, "add_recurring", rule)
    assert dup["result"]["isError"] is True

    listed = _call(server, "list_recurring", {})
    assert listed["result"]["structuredContent"]["count"] == 1
    assert listed["result"]["structuredContent"]["rules"][0]["amount"] == 12.30

    updated = _call(server, "update_recurring", {"id": "kagi-search", "changes": {"status": "paused"}})
    assert updated["result"]["isError"] is False
    assert load_rules(scratch_ledger).rules["kagi-search"].status == "paused"

    deleted = _call(server, "delete_recurring", {"id": "kagi-search"})
    assert deleted["result"]["isError"] is False
    assert list_rules(scratch_ledger) == []


def test_middleware_settles_due_rule_on_any_tool(scratch_ledger, monkeypatch):
    """A due recurring rule is settled by the middleware before any tool runs."""
    from ledger import recurring as recurring_mod
    from ledger.mcp_server import create_server

    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", scratch_ledger)
    monkeypatch.setattr(recurring_mod, "_last_check", 0.0)  # force the sweep
    # due long before today → the middleware must settle it
    add_rule(scratch_ledger, make_rule(next_settle_date=date(2026, 1, 5)))
    server = create_server()

    # list_accounts has nothing to do with recurring -- the middleware still runs
    listed = _call(server, "list_accounts", {})
    assert listed["result"]["isError"] is False

    text = scratch_ledger.read_text()
    assert "2026-01-05 * " in text
    assert "recurring-id: \"kagi-search\"" in text
    assert load_rules(scratch_ledger).rules["kagi-search"].next_settle_date == date(2026, 2, 5)


def test_middleware_is_throttled(scratch_ledger, monkeypatch):
    """The recurring sweep runs at most once per hour."""
    from ledger import recurring as recurring_mod
    from ledger.mcp_server import create_server

    monkeypatch.setattr("ledger.mcp_server.LEDGER_PATH", scratch_ledger)
    add_rule(scratch_ledger, make_rule(next_settle_date=date(2026, 1, 5)))
    server = create_server()
    monkeypatch.setattr(recurring_mod, "_last_check", 0.0)
    _call(server, "list_accounts", {})
    assert "2026-01-05 * " in scratch_ledger.read_text()  # settled once

    # immediately calling again must NOT re-run the sweep
    outcomes = recurring_mod.recurring_check(scratch_ledger, today=date(2026, 9, 6))
    assert outcomes == []
    assert scratch_ledger.read_text().count("recurring-id: \"kagi-search\"") == 1