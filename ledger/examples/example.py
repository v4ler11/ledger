"""Play around with the ledger library against the example ledger.

The read-only demos run against ``examples/main.bean`` (the shipped
ledger). The write demos operate a throwaway *copy* in a temporary
directory, so running this script never mutates the shipped example
files — comments and add_* functions are idempotent across runs.

Run it from the repo root:

    uv run python examples/example.py            # use examples/main.bean
    uv run python examples/example.py some.bean  # use your own ledger
"""

import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

from ledger import (
    LedgerManager,
    add_balance,
    add_account,
    add_commodity,
    add_custom,
    add_event,
    add_note,
    add_price,
    add_query,
    add_transaction,
    bean_check,
    commodity_names,
    date_range,
    ledger_info,
    list_accounts,
    list_commodities,
    list_prices,
    list_tables,
    run_query,
    table_names,
)


def demo_read(mgr: LedgerManager) -> None:
    """Query-only demos against the real ledger."""
    print("== Orientation ==")
    info = ledger_info(mgr)
    print(f"  title                 {info.title}")
    print(f"  operating_currency    {info.operating_currency}")
    print(f"  date range            {info.date_range.first} .. {info.date_range.last}")
    print(f"  account_count         {info.account_count}")
    print(f"  account roots         {', '.join(info.account_roots)}")
    span = date_range(mgr)
    print(f"  (date_range helper)   {span.first} .. {span.last}")

    print("\n== Accounts ==")
    print(", ".join(list_accounts(mgr).accounts))

    print("\n== Commodities ==")
    print(", ".join(list_commodities(mgr).commodities))
    print(f"  commodity_names       {commodity_names(mgr)}")

    print("\n== Prices ==")
    for price in list_prices(mgr).prices:
        print(f"  {price.commodity} = {price.price} @ {price.date}")

    print("\n== Tables ==")
    print(", ".join(table_names(mgr)))
    print(f"  (list_tables warning) {list_tables(mgr).warning}")

    print("\n== BQL queries ==")
    out = run_query(
        mgr, "SELECT account, convert(sum(position), 'USD') "
        "WHERE account ~ 'Assets|Liabilities' GROUP BY account ORDER BY account"
    )
    for row in out.rows:
        print(f"  {row[0]:30} {row[1]}")


def demo_write(src: Path) -> None:
    """Write-binding demos against a throwaway copy (never the shipped ledger)."""
    tmp = Path(tempfile.mkdtemp(prefix="ledger-demo-"))
    for bean in src.parent.glob("*.bean"):
        shutil.copy2(bean, tmp / bean.name)
    ledger = tmp / src.name
    mgr = LedgerManager(ledger)
    errors = mgr.connection_errors()

    print("\n== Write bindings (on a temporary copy, original untouched) ==")
    print(f"  scratch ledger: {ledger}")

    txn, errors = add_transaction(
        ledger,
        date(2024, 3, 31),
        "Coffee beans",
        [("Expenses:Food", "8.50", "USD"), ("Assets:Cash", None)],
        payee="Local Roastery",
        tags={"coffee"},
    )
    print(f"  add_transaction       narration={txn.narration!r} errors={len(errors)}")

    open_, errors = add_account(ledger, date(2024, 4, 1), "Assets:Savings")
    print(f"  add_account           {open_.account} errors={len(errors)}")

    commodity, errors = add_commodity(ledger, date(2024, 4, 1), "BTC")
    print(f"  add_commodity         {commodity.currency} errors={len(errors)}")

    balance, errors = add_balance(
        ledger, date(2024, 4, 1), "Assets:Cash", "991.50", "USD"
    )
    print(f"  add_balance           {balance.account} @ {balance.amount} errors={len(errors)}")

    note, errors = add_note(ledger, date(2024, 4, 1), "Assets:Cash", "called")
    print(f"  add_note              {note.comment!r} errors={len(errors)}")

    price, errors = add_price(ledger, date(2024, 4, 1), "BTC", "50000", "USD")
    print(f"  add_price             {price.currency} = {price.amount} errors={len(errors)}")

    event, errors = add_event(ledger, date(2024, 4, 1), "location", "Paris, France")
    print(f"  add_event             {event.type}={event.description!r} errors={len(errors)}")

    query, errors = add_query(ledger, date(2024, 4, 1), "net", "SELECT account")
    print(f"  add_query             {query.name} errors={len(errors)}")

    custom, errors = add_custom(
        ledger, date(2024, 4, 1), "budget", ["groceries", "45.30"]
    )
    print(f"  add_custom            {custom.type} values={custom.values} errors={len(errors)}")

    print(f"\n  every append re-validated clean ({len(bean_check(mgr).errors)} issues)")
    print("  --- resulting ledger ---")
    print(ledger.read_text())
    shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str]) -> None:
    if len(argv) > 2:
        print("usage: example.py [LEDGER]")
        sys.exit(1)
    root = Path(argv[1]) if len(argv) == 2 else Path(__file__).parent / "main.bean"
    mgr = LedgerManager(root)
    demo_read(mgr)
    demo_write(root)


if __name__ == "__main__":
    main(sys.argv)