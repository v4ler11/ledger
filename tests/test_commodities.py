"""Tests for commodity add/append utilities."""

from datetime import date

from ledger import (
    LedgerManager,
    add_commodity,
    format_commodity,
    list_commodities,
    make_commodity,
)


def test_format_commodity():
    commodity = make_commodity(date(2024, 1, 1), "AAPL")
    assert "2024-01-01 commodity AAPL" in format_commodity(commodity)


def test_add_commodity_appends_and_stays_clean(scratch_ledger):
    commodity, errors = add_commodity(scratch_ledger, date(2024, 9, 1), "BTC")
    assert errors == []
    # routed to the commodities split file, not the main ledger
    assert "commodity BTC" in (scratch_ledger.parent / "commodities.bean").read_text()
    assert "BTC" in list_commodities(LedgerManager(scratch_ledger)).commodities
