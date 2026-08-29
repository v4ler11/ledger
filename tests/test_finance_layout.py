"""Tests mirroring the real finance/*.beancount layout.

The canonical ledger is finance/main.beancount: EUR operating currency,
an include chain commodities/accounts/ledgers/<year> where the yearly
file lives in a subdirectory. The current finance files are empty, so
these tests use a hermetic mirror — both the empty skeleton state and a
populated state — to pin behavior against the real layout.
"""

from pathlib import Path
from typing import Any, cast

import pytest

from ledger import (
    LedgerManager,
    Price,
    ledger_info,
    list_commodities,
    list_prices,
    run_query,
)


def _write_layout(root: Path, populated: bool) -> Path:
    (root / "main.beancount").write_text(
        'option "title" "Personal Ledger"\n'
        'option "operating_currency" "EUR"\n\n'
        'include "commodities.beancount"\n'
        'include "accounts.beancount"\n'
        'include "ledgers/2026.beancount"\n'
    )
    if not populated:
        (root / "commodities.beancount").touch()
        (root / "accounts.beancount").touch()
        (root / "ledgers").mkdir()
        (root / "ledgers" / "2026.beancount").touch()
        return root / "main.beancount"

    (root / "commodities.beancount").write_text(
        "1970-01-01 commodity USD\n"
        "1970-01-01 commodity AAPL\n"
        "2026-02-15 price AAPL 220.00 USD\n"
        "2026-02-16 price USD 0.92 EUR\n"
    )
    (root / "accounts.beancount").write_text(
        "1970-01-01 open Assets:Cash\n"
        "1970-01-01 open Assets:Broker\n"
        "1970-01-01 open Equity:Opening-Balances\n"
        "1970-01-01 open Income:Salary\n"
        "1970-01-01 open Expenses:Food\n"
    )
    (root / "ledgers").mkdir()
    (root / "ledgers" / "2026.beancount").write_text(
        '2026-01-05 * "Opening"\n'
        "  Assets:Cash         10000.00 EUR\n"
        "  Equity:Opening-Balances\n"
        '2026-01-10 * "Salary"\n'
        "  Assets:Cash          3000.00 EUR\n"
        "  Income:Salary\n"
        '2026-02-10 * "Buy AAPL"\n'
        "  Assets:Broker        10 AAPL {210.00 USD}\n"
        "  Assets:Cash\n"
        '2026-02-20 * "Groceries"\n'
        "  Expenses:Food         145.30 EUR\n"
        "  Assets:Cash\n"
    )
    return root / "main.beancount"


@pytest.fixture
def empty_finance(tmp_path: Path) -> Path:
    return _write_layout(tmp_path, populated=False)


@pytest.fixture
def populated_manager(tmp_path: Path) -> LedgerManager:
    return LedgerManager(_write_layout(tmp_path, populated=True))


# ----------------------------------------------------------------------
# Layout / include handling
# ----------------------------------------------------------------------


def test_empty_finance_files_load_clean(empty_finance: Path):
    mgr = LedgerManager(empty_finance)
    assert mgr.connection_errors() == []
    assert mgr.check() == []


def test_subdir_include_is_tracked(populated_manager: LedgerManager):
    root = populated_manager._path.parent
    paths = populated_manager._paths_for_connection(populated_manager.connection())
    assert root / "ledgers" / "2026.beancount" in paths
    assert root / "accounts.beancount" in paths
    assert root / "commodities.beancount" in paths


def test_subdir_include_change_invalidates(populated_manager: LedgerManager):
    conn1 = populated_manager.connection()
    yearly = populated_manager._path.parent / "ledgers" / "2026.beancount"
    with yearly.open("a", encoding="utf-8") as f:
        f.write('2026-03-01 * "More"\n  Expenses:Food  1.00 EUR\n  Assets:Cash\n')

    conn2 = populated_manager.connection()
    assert conn1 is not conn2
    rows = list(cast(Any, conn2.tables)["entries"])
    narrations = {e.narration for e in rows if type(e).__name__ == "Transaction"}
    assert "More" in narrations


# ----------------------------------------------------------------------
# Query helpers against the populated layout
# ----------------------------------------------------------------------


def test_ledger_info_finance(populated_manager: LedgerManager):
    info = ledger_info(populated_manager)
    assert info.error is None
    assert info.operating_currency == "EUR"
    assert info.title == "Personal Ledger"
    assert info.date_range.first == "2026-01-05"
    assert info.date_range.last == "2026-02-20"
    assert info.account_count == 5
    assert info.account_roots == ["Assets", "Equity", "Expenses", "Income"]


def test_list_commodities_finance(populated_manager: LedgerManager):
    out = list_commodities(populated_manager)
    assert out.commodities == ["AAPL", "EUR", "USD"]
    assert out.count == 3


def test_list_prices_finance(populated_manager: LedgerManager):
    prices = list_prices(populated_manager).prices
    by_commodity = {p.commodity: p for p in prices}
    assert by_commodity["AAPL"] == Price(
        commodity="AAPL", date="2026-02-15", price="220.00 USD"
    )
    assert by_commodity["USD"].price == "0.92 EUR"


def test_run_query_finance(populated_manager: LedgerManager):
    out = run_query(
        populated_manager,
        'SELECT account, sum(position) WHERE account ~ "Expenses" '
        "GROUP BY account ORDER BY account",
    )
    assert out.rows == [["Expenses:Food", "(145.30 EUR)"]]  # str(Inventory)
