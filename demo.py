from datetime import date
from pathlib import Path

from ledger import LedgerManager, commodity_names, add_account, add_transaction, bean_check, ledger_info, list_accounts, list_tables, list_prices


FINANCE = Path("/Users/valerii/code/finances")


def main() -> None:
    root = FINANCE / "main.bean"
    mgr = LedgerManager(root)

    print(ledger_info(mgr))

    print(list_accounts(mgr))

    # Open the accounts the demo transaction uses, dated before the
    # transaction so it validates clean.

    # print(bean_check(mgr))

    # print(commodity_names(mgr))

    # print(list_tables(mgr))

    # open_, errors = add_account(root, date(2024, 1, 1), "Assets:Cash")
    # print(f"  add_account Assets:Cash    {open_.account} errors={errors}")
    # open_, errors = add_account(root, date(2024, 1, 1), "Expenses:Food")
    # print(f"  add_account Expenses:Food  {open_.account} errors={errors}")

    # txn, errors = add_transaction(
    #     root,
    #     date(2024, 3, 31),
    #     "Coffee beans",
    #     [("Expenses:Food", "8.50", "GBP"), ("Assets:Cash", None)],
    #     payee="Local Roastery",
    #     tags={"coffee"},
    # )
    # print(txn)
    # print(errors)


if __name__ == "__main__":
    main()