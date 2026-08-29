"""Tests for account open/close utilities."""

from datetime import date

from ledger import (
    LedgerManager,
    add_account,
    close_account,
    format_account,
    list_accounts,
    make_close,
    make_open,
)


def test_format_open_with_currencies_and_booking():
    open_ = make_open(
        date(2024, 1, 1), "Assets:Broker", currencies=["USD", "AAPL"], booking="FIFO"
    )
    rendered = format_account(open_)
    assert "open Assets:Broker" in rendered
    assert "USD" in rendered and "AAPL" in rendered
    assert "FIFO" in rendered
    assert rendered.endswith("\n\n")


def test_make_open_unrestricted():
    open_ = make_open(date(2024, 1, 1), "Assets:Cash")
    assert open_.currencies is None
    assert open_.booking is None
    assert "open Assets:Cash\n" in format_account(open_)


def test_make_close():
    close = make_close(date(2024, 6, 1), "Assets:Cash")
    assert "2024-06-01 close Assets:Cash" in format_account(close)


def test_add_account_appends_and_stays_clean(scratch_ledger):
    open_, errors = add_account(scratch_ledger, date(2024, 5, 1), "Assets:Savings")
    assert errors == []
    # routed to the accounts split file, not the main ledger
    text = (scratch_ledger.parent / "accounts.bean").read_text()
    assert "open Assets:Savings" in text
    accounts = list_accounts(LedgerManager(scratch_ledger)).accounts
    assert "Assets:Savings" in accounts


def test_add_account_with_booking_is_parseable(scratch_ledger):
    open_, errors = add_account(
        scratch_ledger, date(2024, 5, 1), "Assets:Broker2", booking="FIFO"
    )
    assert errors == []
    assert "FIFO" in (scratch_ledger.parent / "accounts.bean").read_text()


def test_close_account_zero_balance(scratch_ledger):
    add_account(scratch_ledger, date(2024, 1, 1), "Assets:Savings")
    close, errors = close_account(scratch_ledger, date(2024, 12, 1), "Assets:Savings")
    assert errors == []
    assert "close Assets:Savings" in (scratch_ledger.parent / "accounts.bean").read_text()
