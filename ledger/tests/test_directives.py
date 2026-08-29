"""Tests for make_/format_/append_/add_ builders of the remaining
directive types (ledger.directives): balance, pad, note, document,
price, event, query, and custom."""

from datetime import date
from decimal import Decimal

from ledger import (
    LedgerManager,
    add_balance,
    add_custom,
    add_document,
    add_event,
    add_note,
    add_price,
    add_query,
    format_balance,
    format_note,
    format_pad,
    format_price,
    make_balance,
    make_note,
    make_pad,
    make_price,
)


def test_format_balance():
    assert (
        "2024-06-01 balance Assets:Cash" in format_balance(make_balance(
            date(2024, 6, 1), "Assets:Cash", "154.20", "USD"
        ))
    )


def test_format_balance_tolerance():
    rendered = format_balance(
        make_balance(
            date(2024, 6, 1),
            "Assets:Cash",
            "319.020",
            "USD",
            tolerance=Decimal("0.002"),
        )
    )
    assert "319.020 ~ 0.002 USD" in rendered


def test_add_balance(scratch_ledger):
    entry, errors = add_balance(
        scratch_ledger, date(2024, 6, 1), "Assets:Cash", "1000.00", "USD"
    )
    assert errors == []
    assert "balance Assets:Cash" in scratch_ledger.read_text()
    assert entry.account == "Assets:Cash"


def test_add_pad(scratch_ledger):
    # A pad is only valid when a following balance assertion needs it to
    # fill a difference, so compose append_pads + add_balance.
    from ledger import add_balance, append_pads

    pad = make_pad(date(2024, 6, 1), "Assets:Cash", "Equity:Opening-Balances")
    assert "pad Assets:Cash Equity:Opening-Balances" in format_pad(pad)
    append_pads(scratch_ledger, [pad])
    # the assertion differs from the real 1000.00 USD balance, so the pad
    # is used and the combined ledger validates clean
    _, errors = add_balance(
        scratch_ledger, date(2024, 6, 2), "Assets:Cash", "154.20", "USD"
    )
    assert errors == []
    assert "pad Assets:Cash Equity:Opening-Balances" in scratch_ledger.read_text()


def test_add_note(scratch_ledger):
    _, errors = add_note(scratch_ledger, date(2024, 6, 1), "Assets:Cash", "called")
    assert errors == []
    assert 'note Assets:Cash "called"' in scratch_ledger.read_text()


def test_add_document(scratch_ledger, tmp_path):
    # the loader verifies the referenced file exists on disk
    stmt = tmp_path / "stmt.pdf"
    stmt.write_text("dummy")
    _, errors = add_document(
        scratch_ledger, date(2024, 6, 1), "Assets:Cash", str(stmt)
    )
    assert errors == []
    assert f'document Assets:Cash "{stmt}"' in scratch_ledger.read_text()


def test_add_price(scratch_ledger):
    entry, errors = add_price(
        scratch_ledger, date(2024, 7, 9), "HOOL", "579.18", "USD"
    )
    assert errors == []
    assert entry.amount.currency == "USD"
    # prices belong to the commodities split file, not the main ledger
    assert "price HOOL" in (scratch_ledger.parent / "commodities.bean").read_text()


def test_add_event(scratch_ledger):
    _, errors = add_event(scratch_ledger, date(2024, 7, 9), "location", "Paris")
    assert errors == []
    assert 'event "location" "Paris"' in scratch_ledger.read_text()


def test_add_query(scratch_ledger):
    _, errors = add_query(
        scratch_ledger,
        date(2024, 7, 9),
        "cash-balances",
        "SELECT account, sum(position)",
    )
    assert errors == []
    assert 'query "cash-balances"' in scratch_ledger.read_text()


def test_add_custom_round_trips(scratch_ledger):
    _, errors = add_custom(
        scratch_ledger,
        date(2024, 7, 9),
        "budget",
        ["groceries", Decimal("45.30")],
    )
    assert errors == []
    text = scratch_ledger.read_text()
    assert 'custom "budget" "groceries" 45.30' in text


def test_add_custom_mixed_values(scratch_ledger):
    # booleans, dates, and amounts also round-trip through a re-validate
    _, errors = add_custom(
        scratch_ledger,
        date(2024, 7, 9),
        "audit",
        [True, date(2024, 1, 1)],
    )
    assert errors == []
    text = scratch_ledger.read_text()
    assert "TRUE" in text and "2024-01-01" in text


def test_append_multiple_and_reload(scratch_ledger):
    entries = [
        make_note(date(2024, 1, 1), "Assets:Cash", "n1"),
        make_price(date(2024, 1, 1), "BTC", "50000", "USD"),
    ]
    from ledger import append_notes, append_prices

    append_notes(scratch_ledger, [entries[0]])
    append_prices(scratch_ledger, [entries[1]])
    mgr = LedgerManager(scratch_ledger)
    assert mgr.connection_errors() == []
    assert format_note(entries[0]).strip().startswith("2024-01-01 note")
    assert format_price(entries[1]).strip().startswith("2024-01-01 price")
