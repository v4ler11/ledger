from datetime import date
from typing import Dict, List, Optional, Set, Tuple

import beanquery

from .ledger import LedgerError, LedgerManager, _format_loader_error
from .models import (
    AccountsList,
    CheckResult,
    CommoditiesList,
    DateRange,
    LedgerInfo,
    LedgerIssue,
    Price,
    PricesList,
    QueryResult,
    TablesList,
)

ROW_LIMIT = 200


def _ledger_error_response(model_cls: type, errors: List[LedgerError]):
    return model_cls(
        error="Ledger has bean-check errors; fix them before querying.",
        error_type="ledger",
        errors=[LedgerIssue(**err) for err in errors],
    )


def _connect(manager: LedgerManager) -> Tuple[beanquery.Connection, List[LedgerError]]:
    """Fetch the cached connection once and derive its errors from it —
    avoids the double staleness check of calling connection_errors() and
    connection() separately."""
    conn = manager.connection()
    errors = [_format_loader_error(err) for err in getattr(conn, "errors", [])]
    return conn, errors


def date_range(manager: LedgerManager) -> DateRange:
    """Min/max posting date. Only sees dates with at least one posting —
    bare open/close/price directives with no postings are invisible here."""
    conn = manager.connection()
    cursor = conn.execute("SELECT min(date), max(date)")
    rows = cursor.fetchall()
    if not rows or rows[0][0] is None:
        return DateRange(first=None, last=None)
    first, last = rows[0]
    return DateRange(first=first.isoformat(), last=last.isoformat())


def ledger_info(manager: LedgerManager) -> LedgerInfo:
    """Orientation facts for querying: base currency, date span, account
    roots, and today's date. Call this before writing BQL to avoid
    guessing the operating currency or today's date.
    """
    conn, errors = _connect(manager)
    if errors:
        return _ledger_error_response(LedgerInfo, errors)

    accounts = account_names(manager)
    roots = sorted({a.split(":")[0] for a in accounts})

    return LedgerInfo(
        today=date.today().isoformat(),
        title=conn.options.get("title"),
        operating_currency=_operating_currency(conn),
        date_range=date_range(manager),
        account_count=len(accounts),
        account_roots=roots,
    )


def run_query(manager: LedgerManager, query: str, offset: int = 0) -> QueryResult:
    """Run an arbitrary BQL query against the ledger.

    Results are capped at ``ROW_LIMIT`` (200) returned rows; pass a
    non-zero ``offset`` to page past the cap when ``truncated`` is true.

    ``columns`` is the list of column names, ``rows`` the row values as
    strings. ``total_rows`` is the exact count only when not truncated.
    """
    conn, errors = _connect(manager)
    if errors:
        return _ledger_error_response(QueryResult, errors)

    try:
        cursor = conn.execute(query)
    except beanquery.Error as exc:
        return QueryResult(error=str(exc), error_type="bql")

    columns = [col.name for col in cursor.description] if cursor.description else []
    skipped = cursor.fetchmany(offset) if offset > 0 else []
    fetched_rows = cursor.fetchmany(ROW_LIMIT + 1)
    truncated = len(fetched_rows) > ROW_LIMIT
    rows = fetched_rows[:ROW_LIMIT]

    return QueryResult(
        columns=columns,
        rows=[[str(v) for v in row] for row in rows],
        truncated=truncated,
        returned_rows=len(rows),
        offset=offset,
        total_rows=None if truncated else len(skipped) + len(rows),
        total_rows_known=not truncated,
    )


def bean_check(manager: LedgerManager) -> CheckResult:
    """Validate the ledger with bean-check.

    Returns structured status and loader/balance errors, reusing the
    cached ledger connection instead of re-parsing from disk.
    """
    errors = manager.connection_errors()
    if not errors:
        return CheckResult(
            ok=True, message="Ledger is clean — no errors or warnings."
        )
    return CheckResult(
        ok=False,
        message=f"Ledger has {len(errors)} error(s).",
        errors=[LedgerIssue(**err) for err in errors],
    )


def _operating_currency(conn: beanquery.Connection) -> Optional[str]:
    currencies = conn.options.get("operating_currency") or []
    return currencies[0] if currencies else None


def account_names(manager: LedgerManager) -> List[str]:
    """Every account declared in the ledger, sorted."""
    conn = manager.connection()
    return sorted(str(row[0]) for row in conn.tables["accounts"])


def table_names(manager: LedgerManager) -> List[str]:
    """BQL-accessible table names, sorted. Note BQL FROM is not a SQL
    table selector."""
    conn = manager.connection()
    return sorted(k for k in conn.tables if k is not None and k != "")


def list_accounts(manager: LedgerManager) -> AccountsList:
    """Return all declared accounts (no 200-row cap)."""
    accounts = account_names(manager)
    return AccountsList(accounts=accounts, count=len(accounts))


def commodity_names(manager: LedgerManager) -> List[str]:
    """Every commodity used in the ledger, sorted: declared via
    ``commodity`` directives, priced in the prices table, or posted as a
    unit currency."""
    conn = manager.connection()
    currencies: Set[str] = set()
    for row in conn.tables["commodities"]:
        currencies.add(row.currency)
    for _, _, _, amount in conn.tables["prices"]:
        currencies.add(amount.currency)
    for row in conn.tables["postings"]:
        currencies.add(row.posting.units.currency)
    return sorted(currencies)


def list_commodities(manager: LedgerManager) -> CommoditiesList:
    """Return all commodities used in the ledger (no 200-row cap)."""
    commodities = commodity_names(manager)
    return CommoditiesList(commodities=commodities, count=len(commodities))


def list_tables(manager: LedgerManager) -> TablesList:
    """Return BQL-accessible table names and the key BQL caveat."""
    return TablesList(
        tables=table_names(manager),
        warning=(
            "In BQL, FROM is a date/filter clause, not a SQL-style table selector."
        ),
    )


def list_prices(manager: LedgerManager) -> PricesList:
    """Latest price per commodity, from the prices table (no 200-row cap)."""
    conn = manager.connection()
    latest: Dict[str, tuple] = {}
    for _, price_date, currency, amount in conn.tables["prices"]:
        prev = latest.get(currency)
        if prev is None or price_date > prev[0]:
            latest[currency] = (price_date, amount)

    prices = [
        Price(
            commodity=currency,
            date=price_date.isoformat(),
            price=str(amount),
        )
        for currency, (price_date, amount) in sorted(latest.items())
    ]
    return PricesList(prices=prices, count=len(prices))
