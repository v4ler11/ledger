"""Tests for LedgerManager: loading, include-aware staleness, invalidation."""

from datetime import date
from typing import Any, cast

from ledger import LedgerManager


def test_connection_clean(sample_path):
    mgr = LedgerManager(sample_path)
    assert mgr.connection_errors() == []
    assert mgr.check() == []


def test_connection_cached_until_stale(sample_path):
    mgr = LedgerManager(sample_path)
    conn1 = mgr.connection()
    conn2 = mgr.connection()
    assert conn1 is conn2  # no reload while unchanged


def test_append_makes_connection_stale(scratch_ledger):
    from ledger import add_transaction

    mgr = LedgerManager(scratch_ledger)
    conn1 = mgr.connection()
    assert add_transaction(
        scratch_ledger,
        date(2024, 12, 1),
        "End of year",
        [("Expenses:Food", "1.00", "USD"), ("Assets:Bank:Checking", None)],
    )[1] == []

    conn2 = mgr.connection()
    assert conn1 is not conn2  # file changed -> reloaded

    entries = list(cast(Any, conn2.tables)["entries"])
    narrations = {
        e.narration for e in entries if type(e).__name__ == "Transaction"
    }
    assert "End of year" in narrations


def test_invalidate_forces_reload(sample_path):
    mgr = LedgerManager(sample_path)
    conn1 = mgr.connection()
    mgr.invalidate()
    conn2 = mgr.connection()
    assert conn1 is not conn2


def test_paths_include_included_file(sample_path):
    mgr = LedgerManager(sample_path)
    conn = mgr.connection()
    paths = mgr._paths_for_connection(conn)
    assert mgr._path in paths
    assert sample_path.parent / "accounts.bean" in paths


def test_include_change_makes_connection_stale(scratch_ledger):
    """A change to an *included* file (finance layout nests includes in
    subdirectories) must also invalidate the cache."""
    mgr = LedgerManager(scratch_ledger)
    conn1 = mgr.connection()
    include = scratch_ledger.parent / "accounts.bean"
    with include.open("a", encoding="utf-8") as f:
        f.write("2024-06-01 open Assets:NewAccount\n")

    conn2 = mgr.connection()
    assert conn1 is not conn2
    accounts = [str(r[0]) for r in cast(Any, conn2.tables)["accounts"]]
    assert "Assets:NewAccount" in accounts
