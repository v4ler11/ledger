"""Tests for layout-aware routing and validate-before-write behavior.

The write bindings route directives to the canonical split files
(``accounts`` / ``commodities`` / yearly ledger) and must never persist
an entry that breaks the ledger: on validation error nothing is written.
"""

from datetime import date
from pathlib import Path

import pytest

from ledger import (
    LedgerManager,
    add_account,
    add_balance,
    add_commodity,
    add_note,
    add_price,
    add_transaction,
    list_accounts,
)
from ledger.layout import KIND_ACCOUNT, KIND_COMMODITY, KIND_TRANSACTION, resolve_target


def _write_finance_layout(root: Path) -> Path:
    """Mirror of finance/: *.beancount files + a ledgers/<year> split."""
    (root / "main.beancount").write_text(
        'option "title" "Personal Ledger"\n'
        'option "operating_currency" "EUR"\n\n'
        'include "commodities.beancount"\n'
        'include "accounts.beancount"\n'
        'include "ledgers/2026.beancount"\n'
    )
    (root / "commodities.beancount").write_text(
        "1970-01-01 commodity USD\n1970-01-01 commodity EUR\n"
    )
    (root / "accounts.beancount").write_text(
        "1970-01-01 open Assets:Cash\n"
        "1970-01-01 open Assets:Broker\n"
        "1970-01-01 open Equity:Opening-Balances\n"
        "1970-01-01 open Expenses:Food\n"
    )
    (root / "ledgers").mkdir()
    (root / "ledgers" / "2026.beancount").write_text(
        '2026-01-05 * "Opening"\n'
        "  Assets:Cash         1000.00 EUR\n"
        "  Equity:Opening-Balances\n"
    )
    return root / "main.beancount"


@pytest.fixture
def finance(tmp_path: Path) -> Path:
    return _write_finance_layout(tmp_path)


def _files(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): p
        for p in root.rglob("*")
        if p.is_file() and p.suffix in (".bean", ".beancount")
    }


def _snapshot(root: Path) -> dict:
    return {name: p.read_bytes() for name, p in _files(root).items()}


# ----------------------------------------------------------------------
# Layout resolution
# ----------------------------------------------------------------------


def test_resolve_account_target(finance):
    base = finance.parent
    assert resolve_target(finance, KIND_ACCOUNT) == base / "accounts.beancount"
    assert resolve_target(finance, KIND_COMMODITY) == base / "commodities.beancount"


def test_resolve_transaction_target_uses_date_year(finance):
    base = finance.parent
    assert (
        resolve_target(finance, KIND_TRANSACTION, date=date(2026, 6, 1))
        == base / "ledgers" / "2026.beancount"
    )
    # a year with no file yet targets a sibling-style file name
    assert (
        resolve_target(finance, KIND_TRANSACTION, date=date(2024, 3, 31))
        == base / "ledgers" / "2024.beancount"
    )


def test_resolve_account_when_path_is_target_file(finance):
    accounts = finance.parent / "accounts.beancount"
    assert resolve_target(accounts, KIND_ACCOUNT) == accounts
    # other kinds still route from the target file's directory
    base = finance.parent
    assert (
        resolve_target(accounts, KIND_TRANSACTION, date=date(2026, 1, 1))
        == base / "ledgers" / "2026.beancount"
    )


def test_resolve_falls_back_to_single_file(tmp_path):
    single = tmp_path / "ledger.bean"
    single.write_text('option "operating_currency" "USD"\n')
    assert resolve_target(single, KIND_ACCOUNT) == single.resolve()
    assert resolve_target(single, KIND_COMMODITY) == single.resolve()
    assert (
        resolve_target(single, KIND_TRANSACTION, date=date(2024, 1, 1))
        == single.resolve()
    )


# ----------------------------------------------------------------------
# Routing of add_* writes
# ----------------------------------------------------------------------


def test_add_account_routes_to_accounts_file(finance):
    open_, errors = add_account(finance, date(2024, 4, 1), "Assets:Savings")
    assert errors == []
    assert open_.account == "Assets:Savings"
    text = (finance.parent / "accounts.beancount").read_text()
    assert "open Assets:Savings" in text
    assert "open Assets:Savings" not in finance.read_text()
    assert "Assets:Savings" in list_accounts(LedgerManager(finance)).accounts


def test_add_commodity_and_price_route_to_commodities_file(finance):
    _, errors = add_commodity(finance, date(2024, 4, 1), "BTC")
    assert errors == []
    _, errors = add_price(finance, date(2024, 4, 1), "BTC", "50000", "USD")
    assert errors == []
    text = (finance.parent / "commodities.beancount").read_text()
    assert "commodity BTC" in text
    assert "price BTC" in text
    assert "BTC" not in finance.read_text()


def test_add_transaction_routes_to_year_file_and_wires_include(finance):
    _, errors = add_transaction(
        finance,
        date(2026, 6, 1),
        "Coffee",
        [("Expenses:Food", "8.50", "EUR"), ("Assets:Cash", None)],
    )
    assert errors == []
    yearly = finance.parent / "ledgers" / "2026.beancount"
    assert '2026-06-01 * "Coffee"' in yearly.read_text()
    assert "Coffee" not in finance.read_text()
    # the year file was already included, so no include line is duplicated
    assert finance.read_text().count('include "ledgers/2026.beancount"') == 1


def test_add_transaction_to_new_year_wires_include(finance):
    _, errors = add_transaction(
        finance,
        date(2024, 3, 31),
        "Historical",
        [("Expenses:Food", "8.50", "EUR"), ("Assets:Cash", None)],
    )
    assert errors == []
    base = finance.parent
    assert (base / "ledgers" / "2024.beancount").exists()
    assert 'include "ledgers/2024.beancount"' in finance.read_text()
    # still exactly one include line for the new file
    assert finance.read_text().count('include "ledgers/2024.beancount"') == 1


def test_second_add_to_new_year_does_not_duplicate_include(finance):
    add_transaction(
        finance,
        date(2024, 3, 31),
        "First",
        [("Expenses:Food", "8.50", "EUR"), ("Assets:Cash", None)],
    )
    _, errors = add_transaction(
        finance,
        date(2024, 4, 5),
        "Second",
        [("Expenses:Food", "3.00", "EUR"), ("Assets:Cash", None)],
    )
    assert errors == []
    main_text = finance.read_text()
    assert main_text.count('include "ledgers/2024.beancount"') == 1
    assert (finance.parent / "ledgers" / "2024.beancount").read_text().count(
        "Second"
    ) == 1


def test_new_year_covered_by_glob_include_gets_no_literal_duplicate(finance):
    base = finance.parent
    # root loads yearly files via an include glob instead of literals
    (base / "main.beancount").write_text(
        'option "title" "Personal Ledger"\n'
        'option "operating_currency" "EUR"\n\n'
        'include "commodities.beancount"\n'
        'include "accounts.beancount"\n'
        'include "ledgers/*.beancount"\n'
    )
    _, errors = add_transaction(
        finance,
        date(2027, 2, 1),
        "Future",
        [("Expenses:Food", "8.50", "EUR"), ("Assets:Cash", None)],
    )
    assert errors == []
    year = base / "ledgers" / "2027.beancount"
    assert year.exists()
    assert '2027-02-01 * "Future"' in year.read_text()
    # the glob already loads the new file; a literal include would make
    # beancount parse it twice and fail
    assert 'include "ledgers/2027.beancount"' not in finance.read_text()
    # ledger stays valid for further adds to the same year
    _, errors = add_transaction(
        finance,
        date(2027, 3, 1),
        "More",
        [("Expenses:Food", "3.00", "EUR"), ("Assets:Cash", None)],
    )
    assert errors == []


def test_other_dated_directives_route_to_year_file(finance):
    _, errors = add_note(finance, date(2026, 6, 2), "Assets:Cash", "called")
    assert errors == []
    _, errors = add_balance(
        finance, date(2026, 6, 2), "Assets:Cash", "1000.00", "EUR"
    )
    assert errors == []
    year = (finance.parent / "ledgers" / "2026.beancount").read_text()
    assert 'note Assets:Cash "called"' in year
    assert "balance Assets:Cash" in year


# ----------------------------------------------------------------------
# No-write-on-error
# ----------------------------------------------------------------------


def test_error_write_leaves_every_file_untouched(finance):
    before = _snapshot(finance.parent)
    # a transaction dated before Assets:Cash was opened (1970-01-01 open is
    # in accounts), against an account that does not exist at all
    txn, errors = add_transaction(
        finance,
        date(2026, 7, 1),
        "Ghost spend",
        [("Expenses:Unknown", "1.00", "EUR"), ("Assets:Cash", None)],
    )
    assert errors, "expected validation errors"
    after = _snapshot(finance.parent)
    assert after == before, "a failed add must not modify any ledger file"
    # and the new yearly file must not have been created
    assert not (finance.parent / "ledgers" / "2026.beancount").read_text().count(
        "Ghost spend"
    )


def test_error_reference_mentions_real_file_path(finance):
    _, errors = add_transaction(
        finance,
        date(2026, 7, 1),
        "Ghost spend",
        [("Expenses:Unknown", "1.00", "EUR"), ("Assets:Cash", None)],
    )
    assert errors
    assert "ledger-check" not in "".join(str(err["file"] or "") for err in errors)
    assert str(finance.parent / "ledgers" / "2026.beancount") in "".join(
        str(err["file"] or "") for err in errors
    )


def test_bootstraps_new_single_file_ledger(tmp_path):
    fresh = tmp_path / "fresh.bean"
    open_, errors = add_account(fresh, date(2024, 1, 1), "Assets:Cash")
    assert errors == []
    assert open_.account == "Assets:Cash"
    assert "open Assets:Cash" in fresh.read_text()
    # once the ledger exists, validation is live again
    _, errors = add_transaction(
        fresh,
        date(2024, 1, 2),
        "Bad",
        [("Expenses:Unknown", "1.00", "USD"), ("Assets:Cash", None)],
    )
    assert errors
    assert "Bad" not in fresh.read_text()