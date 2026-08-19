"""Create and append Beancount v3 transactions programmatically.

Build `beancount.core.data.Transaction` objects with plain Python values,
format them exactly like a hand-written ledger, and append them to the
ledger file. The resulting file is re-validated so you never silently
write a broken ledger.

The posting spec is a tuple: ``(account, number, currency)``, or
``(account, None)`` for a balancing posting whose amount is computed by
Beancount from the other legs. An optional fourth element ``cost`` is a
``(number, currency)`` or ``(number, currency, date, label)`` tuple for
lot-tracked holdings (stocks, crypto).
"""

import io
from datetime import date as _date
from decimal import Decimal
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple, Union

from beancount.core import amount, data, position
from beancount.parser import printer

from .layout import (
    KIND_TRANSACTION,
    append_include,
    resolve_target,
    validate_append,
)
from .ledger import LedgerError


# (account, number, currency)  -> a posted amount
# (account, None)              -> balancing leg, amount filled in by Beancount
# (account, number, currency, cost) -> lot-tracked posting, cost = (number, currency[, date, label])
PostingSpec = Union[
    Tuple[str, object, object, object],
    Tuple[str, object, object],
    Tuple[str, None],
]


CostSpec = Union[Tuple[str, str], Tuple[str, str, object, object]]


def _as_decimal(number: object) -> Decimal:
    if isinstance(number, Decimal):
        return number
    return Decimal(str(number))


def _make_cost(cost: CostSpec) -> position.Cost:
    number, currency = cost[0], cost[1]
    if len(cost) == 4:
        cost_date, cost_label = cost[2], cost[3]
    else:
        cost_date, cost_label = None, None
    return position.Cost(_as_decimal(number), currency, cost_date, cost_label)


def make_posting(spec: PostingSpec) -> data.Posting:
    """Build a Posting from a ``(account, number, currency[, cost])`` tuple.

    ``number`` of ``None`` produces a balancing posting (units=None) whose
    amount Beancount derives from the other legs of the transaction.
    """
    account = spec[0]
    number = spec[1]
    units: Optional[amount.Amount] = None
    cost: Optional[position.Cost] = None
    if number is not None:
        currency = spec[2]
        units = amount.Amount(_as_decimal(number), currency)
        if len(spec) == 4 and spec[3] is not None:
            cost = _make_cost(spec[3])
    return data.Posting(account, units, cost, None, None, None)


def make_transaction(
    date: _date,
    narration: str,
    postings: Union[Iterable[PostingSpec], Iterable[data.Posting]],
    *,
    payee: Optional[str] = None,
    flag: str = "*",
    tags: Optional[Set[str]] = None,
    links: Optional[Set[str]] = None,
    filename: str = "<transaction>",
) -> data.Transaction:
    """Build a Beancount ``Transaction`` from plain tuples.

    Each posting is either ``(account, number, currency[, cost])`` or
    ``(account, None)`` (the balancing leg). Pass ready-made
    ``data.Posting`` objects instead of tuples for full control.
    """
    built: List[data.Posting] = []
    for posting in postings:
        if isinstance(posting, data.Posting):
            built.append(posting)
        else:
            built.append(make_posting(posting))
    return data.Transaction(
        meta=data.new_metadata(filename, 0),
        date=date,
        flag=flag,
        payee=payee,
        narration=narration,
        tags=frozenset(tags or ()),
        links=frozenset(links or ()),
        postings=built,
    )


def format_transaction(txn: data.Transaction) -> str:
    """Render a Transaction exactly as it would appear in a ledger file.

    Includes the trailing blank line Beancount uses between directives.
    """
    buf = io.StringIO()
    printer.print_entry(txn, file=buf)
    return buf.getvalue()


def append_transactions(
    path: Union[str, Path], txns: Iterable[data.Transaction]
) -> None:
    """Append formatted transactions to the ledger file.

    The file is opened in append mode and flushed, so a change is visible
    to the next ``LedgerManager`` staleness check.
    """
    path = Path(path)
    rendered = "".join(format_transaction(txn) for txn in txns)
    with path.open("a", encoding="utf-8") as f:
        f.write(rendered)
        f.flush()


def add_transaction(
    path: Union[str, Path],
    date: _date,
    narration: str,
    postings: Union[Iterable[PostingSpec], Iterable[data.Posting]],
    *,
    payee: Optional[str] = None,
    flag: str = "*",
    tags: Optional[Set[str]] = None,
    links: Optional[Set[str]] = None,
) -> Tuple[data.Transaction, List[LedgerError]]:
    """Make a transaction in the yearly ledger file, validating first.

    The transaction is routed to ``ledgers/<YEAR>.bean`` (or the layout's
    yearly directory) when present, else to the ledger root. It is
    staged-validated before any write; on error the ledger is untouched.

    Returns ``(txn, errors)`` where ``errors`` is the list of loader errors
    from validating the prospective update. An empty list means it was
    written and the ledger stays clean.
    """
    target = resolve_target(path, KIND_TRANSACTION, date=date)
    txn = make_transaction(
        date,
        narration,
        list(postings),
        payee=payee,
        flag=flag,
        tags=tags,
        links=links,
        filename=str(target),
    )
    rendered = format_transaction(txn)
    errors, include_line = validate_append(path, target, rendered)
    if not errors:
        append_transactions(target, [txn])
        if include_line is not None:
            append_include(path, include_line)
    return txn, errors
