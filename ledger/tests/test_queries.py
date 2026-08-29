"""Tests for the high-level BQL query helpers (ledger.queries)."""

import pytest

from ledger import (
    LedgerManager,
    Price,
    bean_check,
    commodity_names,
    date_range,
    ledger_info,
    list_accounts,
    list_balances,
    list_commodities,
    list_prices,
    list_tables,
    run_query,
)


@pytest.fixture
def mgr(sample_path) -> LedgerManager:
    return LedgerManager(sample_path)


def test_ledger_info(mgr):
    info = ledger_info(mgr)
    assert info.error is None
    assert info.operating_currency == "USD"
    assert info.date_range.first == "2024-01-01"
    assert info.account_count >= 7
    assert "Assets" in info.account_roots


def test_date_range(mgr):
    assert date_range(mgr).first == "2024-01-01"
    assert date_range(mgr).last == "2024-03-20"


def test_run_query_rows(mgr):
    out = run_query(
        mgr, "SELECT account, sum(position) GROUP BY account ORDER BY account"
    )
    assert out.error is None
    assert out.columns == ["account", "sum(position)"]
    assert any(row[0] == "Assets:Cash" for row in out.rows)
    assert out.truncated is False


def test_run_query_invalid_bql(mgr):
    out = run_query(mgr, "SELECT nope FROM nowhere")
    assert out.error_type == "bql"
    assert out.error is not None


def test_run_query_paging(mgr):
    out = run_query(
        mgr, 'SELECT date, narration WHERE account ~ "Assets" ORDER BY date', offset=1
    )
    assert out.offset == 1
    assert out.total_rows == out.returned_rows + 1


def test_bean_check_clean(mgr):
    out = bean_check(mgr)
    assert out.ok is True
    assert out.errors == []


def test_list_accounts_and_tables(mgr):
    accounts = list_accounts(mgr)
    assert "Assets:Cash" in accounts.accounts
    assert accounts.count == len(accounts.accounts)
    tables = list_tables(mgr)
    assert "postings" in tables.tables


def test_list_balances(mgr):
    out = list_balances(mgr)
    assert set(out) == set(list_accounts(mgr).accounts)
    assert out["Assets:Cash"] == ["1000.00 USD"]
    assert out["Assets:Bank:Checking"] == ["-320.45 USD"]
    # costed holding appears as its units, not a converted value
    assert out["Assets:Broker"] == ["10 AAPL"]
    assert out["Expenses:Food"] == ["120.45 USD"]


def test_list_commodities(mgr):
    out = list_commodities(mgr)
    assert out.commodities == ["AAPL", "USD"]
    assert out.count == 2
    assert commodity_names(mgr) == ["AAPL", "USD"]


def test_list_prices(mgr):
    prices = list_prices(mgr)
    assert Price(commodity="AAPL", date="2024-02-15", price="185.00 USD") in (
        prices.prices
    )


def test_bean_check_reflects_errors(tmp_path):
    ledger = tmp_path / "bad.bean"
    ledger.write_text(
        '2024-01-01 * "Unbalanced"\n'
        "  Assets:Cash   10.00 USD\n"  # no balancing leg
    )
    out = bean_check(LedgerManager(ledger))
    assert out.ok is False
    assert len(out.errors) >= 1
