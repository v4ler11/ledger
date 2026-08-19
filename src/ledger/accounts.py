"""Create and append Beancount v3 account directives.

Build `beancount.core.data.Open` / `Close` objects (``1970-01-01 open
Assets:Cash``), format them like a hand-written ledger, and append them
to the ledger file. The resulting file is re-validated so you never
silently write a broken ledger.
"""

import io
from datetime import date as _date
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union

from beancount.core import data
from beancount.parser import printer

from .layout import (
    KIND_ACCOUNT,
    append_include,
    resolve_target,
    validate_append,
)
from .ledger import LedgerError

AccountEntry = Union[data.Open, data.Close]


def _coerce_currencies(currencies: Optional[Iterable[str]]) -> Optional[frozenset]:
    if currencies is None:
        return None
    if isinstance(currencies, frozenset):
        return currencies
    return frozenset(currencies)


def _coerce_booking(booking) -> object:
    """Accept ``None``, a ``Booking`` enum, or a booking-method name."""
    if booking is None:
        return None
    if isinstance(booking, data.Booking):
        return booking
    if not isinstance(booking, str):
        raise TypeError(f"booking must be a Booking or str, got {type(booking).__name__}")
    try:
        return data.Booking[booking.upper()]
    except KeyError:
        raise ValueError(f"unknown booking method: {booking!r}")


def make_open(
    date: _date,
    account: str,
    *,
    currencies: Optional[Iterable[str]] = None,
    booking: Optional[Union[str, data.Booking]] = None,
    filename: str = "<open>",
) -> data.Open:
    """Build a Beancount ``Open`` directive for ``account``.

    ``currencies`` restricts the account to those commodities (omit for an
    unrestricted account); ``booking`` is a lot-booking method — ``None``,
    or a name like ``"FIFO"`` / ``"STRICT"`` (case-insensitive).
    """
    return data.Open(
        meta=data.new_metadata(filename, 0),
        date=date,
        account=account,
        currencies=_coerce_currencies(currencies),
        booking=_coerce_booking(booking),
    )


def make_close(
    date: _date,
    account: str,
    *,
    filename: str = "<close>",
) -> data.Close:
    """Build a Beancount ``Close`` directive for ``account``."""
    return data.Close(
        meta=data.new_metadata(filename, 0),
        date=date,
        account=account,
    )


def format_account(entry: AccountEntry) -> str:
    """Render an Open or Close directive exactly as in a ledger file.

    Includes the trailing blank line Beancount uses between directives.
    """
    buf = io.StringIO()
    printer.print_entry(entry, file=buf)
    return buf.getvalue()


def append_accounts(
    path: Union[str, Path], entries: Iterable[AccountEntry]
) -> None:
    """Append formatted Open/Close directives to the ledger file.

    The file is opened in append mode and flushed, so a change is visible
    to the next ``LedgerManager`` staleness check.
    """
    path = Path(path)
    rendered = "".join(format_account(entry) for entry in entries)
    with path.open("a", encoding="utf-8") as f:
        f.write(rendered)
        f.flush()


def add_account(
    path: Union[str, Path],
    date: _date,
    account: str,
    *,
    currencies: Optional[Iterable[str]] = None,
    booking: Optional[Union[str, data.Booking]] = None,
) -> Tuple[data.Open, List[LedgerError]]:
    """Open an account in the ledger's accounts file and validate first.

    The directive is routed to the accounts split file (``accounts.bean``
    next to the root) when the layout has one. The appended file is
    *staged* and re-parsed before anything is written; on error the
    ledger is left untouched and ``errors`` is returned instead.

    Returns ``(open_, errors)`` where ``errors`` is the list of loader
    errors from the prospective update (an empty list means it was
    written cleanly). A broker account is typically opened with
    ``booking="FIFO"`` and its commodities restricted.
    """
    target = resolve_target(path, KIND_ACCOUNT)
    open_ = make_open(
        date, account, currencies=currencies, booking=booking, filename=str(target)
    )
    rendered = format_account(open_)
    errors, include_line = validate_append(path, target, rendered)
    if not errors:
        append_accounts(target, [open_])
        if include_line is not None:
            append_include(path, include_line)
    return open_, errors


def close_account(
    path: Union[str, Path],
    date: _date,
    account: str,
) -> Tuple[data.Close, List[LedgerError]]:
    """Close an account in the accounts split file, validating first.

    Returns ``(close, errors)``; an empty ``errors`` list means the
    updated ledger is clean and the directive was written. On error the
    ledger is not modified.
    """
    target = resolve_target(path, KIND_ACCOUNT)
    close = make_close(date, account, filename=str(target))
    rendered = format_account(close)
    errors, include_line = validate_append(path, target, rendered)
    if not errors:
        append_accounts(target, [close])
        if include_line is not None:
            append_include(path, include_line)
    return close, errors
