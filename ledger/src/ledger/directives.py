"""Create and append Beancount v3 directive types.

Builders for the remaining directive types in the Beancount language
manual — balance, pad, note, document, price, event, query, and custom —
mirroring the ``transactions`` / ``accounts`` / ``commodities`` modules.
Each type exposes ``make_/format_/append_/add_`` so you can build a
directive, render it like a hand-written ledger, append several at once,
or add one with re-validation (you never silently write a broken ledger).
"""

import io
from datetime import date as _date
from decimal import Decimal
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union

from beancount.core import amount, data
from beancount.parser import printer
from beancount.parser.grammar import ValueType

from .layout import (
    KIND_COMMODITY,
    KIND_TRANSACTION,
    append_include,
    resolve_target,
    validate_append,
)
from .ledger import LedgerError


def _decimal(number: object) -> Decimal:
    if isinstance(number, Decimal):
        return number
    return Decimal(str(number))


def _format(entry) -> str:
    """Render the directive exactly as it would appear in a ledger file,
    including the trailing blank line Beancount uses between directives."""
    buf = io.StringIO()
    printer.print_entry(entry, file=buf)
    return buf.getvalue()


def _append(path: Path, rendered: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(rendered)
        f.flush()


def _commit(root, kind, date, entry):
    """Stage-validate ``entry`` and append it to its layout target.

    The entry is routed to the layout file for ``kind`` (``accounts`` /
    ``commodities`` / yearly ledger), staged-validated, and only written
    when the prospective ledger parses clean. On error the ledger is
    untouched and the errors are returned; the entry's metadata filename
    is rewritten to the chosen target so errors mention the real file.
    """
    target = resolve_target(root, kind, date=date)
    entry.meta["filename"] = str(target)
    rendered = _format(entry)
    errors, include_line = validate_append(root, target, rendered)
    if not errors:
        _append(target, rendered)
        if include_line is not None:
            append_include(root, include_line)
    return entry, errors


# ----------------------------------------------------------------------
# Balance
# ----------------------------------------------------------------------


def make_balance(
    date: _date,
    account: str,
    number: object,
    currency: str,
    *,
    tolerance: Optional[Decimal] = None,
    filename: str = "<balance>",
) -> data.Balance:
    """Build a ``balance <account> <number> <currency>`` assertion.

    ``tolerance`` optionally loosens the assertion with a ``~ <amount>``
    clause (e.g. ``319.020 ~ 0.002 USD``).
    """
    return data.Balance(
        meta=data.new_metadata(filename, 0),
        date=date,
        account=account,
        amount=amount.Amount(_decimal(number), currency),
        tolerance=_decimal(tolerance) if tolerance is not None else None,
        diff_amount=None,
    )


def format_balance(entry: data.Balance) -> str:
    return _format(entry)


def append_balances(path: Union[str, Path], entries: Iterable[data.Balance]) -> None:
    path = Path(path)
    _append(path, "".join(format_balance(e) for e in entries))


def add_balance(
    path: Union[str, Path],
    date: _date,
    account: str,
    number: object,
    currency: str,
    *,
    tolerance: Optional[Decimal] = None,
) -> Tuple[data.Balance, List[LedgerError]]:
    """Route a balance assertion to the yearly ledger and validate first.

    Returns ``(entry, errors)``; an empty ``errors`` list means it was
    written cleanly. On error nothing is written.
    """
    entry = make_balance(
        date, account, number, currency, tolerance=tolerance, filename=str(path)
    )
    return _commit(path, KIND_TRANSACTION, date, entry)


# ----------------------------------------------------------------------
# Pad
# ----------------------------------------------------------------------


def make_pad(
    date: _date,
    account: str,
    source_account: str,
    *,
    filename: str = "<pad>",
) -> data.Pad:
    """Build a ``pad <account> <source_account>`` directive.

    It makes the following balance assertion on ``account`` succeed by
    inserting the shortfall from ``source_account`` (usually an Equity
    account).
    """
    return data.Pad(
        meta=data.new_metadata(filename, 0),
        date=date,
        account=account,
        source_account=source_account,
    )


def format_pad(entry: data.Pad) -> str:
    return _format(entry)


def append_pads(path: Union[str, Path], entries: Iterable[data.Pad]) -> None:
    path = Path(path)
    _append(path, "".join(format_pad(e) for e in entries))


def add_pad(
    path: Union[str, Path],
    date: _date,
    account: str,
    source_account: str,
) -> Tuple[data.Pad, List[LedgerError]]:
    """Route a pad directive to the yearly ledger and validate first."""
    entry = make_pad(date, account, source_account, filename=str(path))
    return _commit(path, KIND_TRANSACTION, date, entry)


# ----------------------------------------------------------------------
# Note
# ----------------------------------------------------------------------


def make_note(
    date: _date,
    account: str,
    comment: str,
    *,
    filename: str = "<note>",
) -> data.Note:
    """Build a ``note <account> "comment"`` directive — a dated comment
    attached to an account's journal."""
    return data.Note(
        meta=data.new_metadata(filename, 0),
        date=date,
        account=account,
        comment=comment,
        tags=None,
        links=None,
    )


def format_note(entry: data.Note) -> str:
    return _format(entry)


def append_notes(path: Union[str, Path], entries: Iterable[data.Note]) -> None:
    path = Path(path)
    _append(path, "".join(format_note(e) for e in entries))


def add_note(
    path: Union[str, Path],
    date: _date,
    account: str,
    comment: str,
) -> Tuple[data.Note, List[LedgerError]]:
    """Route a note directive to the yearly ledger and validate first."""
    entry = make_note(date, account, comment, filename=str(path))
    return _commit(path, KIND_TRANSACTION, date, entry)


# ----------------------------------------------------------------------
# Document
# ----------------------------------------------------------------------


def make_document(
    date: _date,
    account: str,
    path: str,
    *,
    filename: str = "<document>",
) -> data.Document:
    """Build a ``document <account> "/path/to/file"`` directive that
    attaches an external file to an account's journal."""
    return data.Document(
        meta=data.new_metadata(filename, 0),
        date=date,
        account=account,
        filename=path,
        tags=None,
        links=None,
    )


def format_document(entry: data.Document) -> str:
    return _format(entry)


def append_documents(path: Union[str, Path], entries: Iterable[data.Document]) -> None:
    path = Path(path)
    _append(path, "".join(format_document(e) for e in entries))


def add_document(
    path: Union[str, Path],
    date: _date,
    account: str,
    doc_path: str,
) -> Tuple[data.Document, List[LedgerError]]:
    """Route a document directive to the yearly ledger and validate first."""
    entry = make_document(date, account, doc_path, filename=str(path))
    return _commit(path, KIND_TRANSACTION, date, entry)


# ----------------------------------------------------------------------
# Price
# ----------------------------------------------------------------------


def make_price(
    date: _date,
    currency: str,
    number: object,
    quote_currency: str,
    *,
    filename: str = "<price>",
) -> data.Price:
    """Build a ``price <currency> <number> <quote_currency>`` directive —
    e.g. ``price HOOL 579.18 USD`` says one HOOL is worth 579.18 USD."""
    return data.Price(
        meta=data.new_metadata(filename, 0),
        date=date,
        currency=currency,
        amount=amount.Amount(_decimal(number), quote_currency),
    )


def format_price(entry: data.Price) -> str:
    return _format(entry)


def append_prices(path: Union[str, Path], entries: Iterable[data.Price]) -> None:
    path = Path(path)
    _append(path, "".join(format_price(e) for e in entries))


def add_price(
    path: Union[str, Path],
    date: _date,
    currency: str,
    number: object,
    quote_currency: str,
) -> Tuple[data.Price, List[LedgerError]]:
    """Route a price directive to the commodities file and validate first."""
    entry = make_price(date, currency, number, quote_currency, filename=str(path))
    return _commit(path, KIND_COMMODITY, date, entry)


# ----------------------------------------------------------------------
# Event
# ----------------------------------------------------------------------


def make_event(
    date: _date,
    type: str,
    description: str,
    *,
    filename: str = "<event>",
) -> data.Event:
    """Build an ``event "<type>" "<description>"`` directive — a dated
    value for a named event, e.g. ``event "location" "Paris, France"``."""
    return data.Event(
        meta=data.new_metadata(filename, 0),
        date=date,
        type=type,
        description=description,
    )


def format_event(entry: data.Event) -> str:
    return _format(entry)


def append_events(path: Union[str, Path], entries: Iterable[data.Event]) -> None:
    path = Path(path)
    _append(path, "".join(format_event(e) for e in entries))


def add_event(
    path: Union[str, Path],
    date: _date,
    type: str,
    description: str,
) -> Tuple[data.Event, List[LedgerError]]:
    """Route an event directive to the yearly ledger and validate first."""
    entry = make_event(date, type, description, filename=str(path))
    return _commit(path, KIND_TRANSACTION, date, entry)


# ----------------------------------------------------------------------
# Query
# ----------------------------------------------------------------------


def make_query(
    date: _date,
    name: str,
    query_string: str,
    *,
    filename: str = "<query>",
) -> data.Query:
    """Build a ``query "<name>" "<bql>"`` directive — a named BQL query
    stored in the ledger, run against the ledger up to ``date``."""
    return data.Query(
        meta=data.new_metadata(filename, 0),
        date=date,
        name=name,
        query_string=query_string,
    )


def format_query(entry: data.Query) -> str:
    return _format(entry)


def append_queries(path: Union[str, Path], entries: Iterable[data.Query]) -> None:
    path = Path(path)
    _append(path, "".join(format_query(e) for e in entries))


def add_query(
    path: Union[str, Path],
    date: _date,
    name: str,
    query_string: str,
) -> Tuple[data.Query, List[LedgerError]]:
    """Route a query directive to the yearly ledger and validate first."""
    entry = make_query(date, name, query_string, filename=str(path))
    return _commit(path, KIND_TRANSACTION, date, entry)


# ----------------------------------------------------------------------
# Custom
# ----------------------------------------------------------------------


def _coerce_custom_value(value: object) -> ValueType:
    """Wrap a Python value as a Beancount custom ``ValueType(value, dtype)``.

    Numbers become ``Decimal``, strings/booleans/dates/Amounts keep their
    natural type; anything else is wrapped with its own type so the
    printer can render it.
    """
    if isinstance(value, ValueType):
        return value
    if isinstance(value, bool):
        return ValueType(value, bool)
    if isinstance(value, (int, float, Decimal)):
        return ValueType(_decimal(value), Decimal)
    if isinstance(value, _date):
        return ValueType(value, _date)
    if isinstance(value, amount.Amount):
        return ValueType(value, amount.Amount)
    if isinstance(value, str):
        return ValueType(value, str)
    return ValueType(value, type(value))


def make_custom(
    date: _date,
    type: str,
    values: Optional[Iterable] = None,
    *,
    filename: str = "<custom>",
) -> data.Custom:
    """Build a ``custom "<type>" <values...>`` directive.

    ``values`` is an iterable of strings, numbers, booleans, dates, or
    Amounts; they are coerced to Beancount's custom ``ValueType``s.
    """
    return data.Custom(
        meta=data.new_metadata(filename, 0),
        date=date,
        type=type,
        values=[_coerce_custom_value(v) for v in (values or [])],
    )


def format_custom(entry: data.Custom) -> str:
    return _format(entry)


def append_customs(path: Union[str, Path], entries: Iterable[data.Custom]) -> None:
    path = Path(path)
    _append(path, "".join(format_custom(e) for e in entries))


def add_custom(
    path: Union[str, Path],
    date: _date,
    type: str,
    values: Optional[Iterable] = None,
) -> Tuple[data.Custom, List[LedgerError]]:
    """Route a custom directive to the yearly ledger and validate first."""
    entry = make_custom(date, type, values, filename=str(path))
    return _commit(path, KIND_TRANSACTION, date, entry)
