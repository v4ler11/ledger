"""Create and append Beancount v3 transactions programmatically.

Build `beancount.core.data.Transaction` objects with plain Python values,
format them exactly like a hand-written ledger, and append them to the
ledger file. The resulting file is re-validated so you never silently
write a broken ledger.

Each posting is a full dict spec — ``{account, number, currency, cost,
price, flag, meta}`` — or a shorthand tuple ``(account, number,
currency[, cost])`` / ``(account, None)``. ``cost`` and ``price`` support
per-unit and total forms plus date/label lot-matching filters; see
:func:`make_posting` and :func:`_make_cost`. An elided posting (``number``
is ``None``) is left open in the file — the missing amount is interpolated
by Beancount at load time so the transaction balances, it is not computed
here.
"""

import io
from datetime import date as _date
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple, Union

from beancount.core import amount, data, position
from beancount.parser import printer

from .layout import (
    KIND_TRANSACTION,
    append_include,
    resolve_target,
    validate_append,
)
from .ledger import LedgerError


# Full-control posting spec: every optional key is honoured.
#   * number=None (or omitted) -> elided balancing leg; at most one per txn.
#   * cost:  (number, currency) | (number, currency, date, label)
#            | CostSpec | {"number":..., "currency":..., "date":..., "label":...}
#            | {"date":...} | {"label":...} | {}  (lot-matching filters)
#   * price: (number, currency) per-unit, or a {"total":..., "currency":...}
#            (the total form is divided by units to a per-unit price)
#   * flag:  per-posting flag overriding the transaction flag (reconcile/dedup)
#   * meta:  per-posting Key: Value metadata dict
PostingDict = Dict[str, object]
PostingSpec = Union[
    Tuple[str, object, object],        # (account, number, currency)
    Tuple[str, None],                  # elided balancing leg
    Tuple[str, object, object, object, object, object, object],  # full tuple form
    PostingDict,
]

# cost: (number, currency) | (number, currency, date, label) | CostSpec | dict
CostSpec = Union[
    Tuple[object, str],
    Tuple[object, str, object, object],
    "position.CostSpec",
    Dict[str, object],
]


def _as_decimal(number: object) -> Decimal:
    if isinstance(number, Decimal):
        return number
    return Decimal(str(number))


def _make_cost(spec: CostSpec) -> Optional[position.CostSpec]:
    """Normalise a cost spec into a v3 ``position.CostSpec``.

    ``{183.07 USD}``        -> "number", "currency"
    ``{183.07 USD, "ref"}`` -> number/currency + label (4-tuple)
    ``{2014-02-11}``        -> date-only                       (lot-matching filter)
    ``{"ref-001"}``         -> label-only                      (lot-matching filter)
    ``{}``                  -> empty spec, matches all lots

    Accepts a dict (CostSpec kwargs), a ``position.CostSpec`` passthrough, a
    ``(number, currency)`` tuple, or the legacy ``(number, currency, date,
    label)`` tuple.
    """
    if spec is None:
        return None
    if isinstance(spec, position.CostSpec):
        return spec
    if isinstance(spec, (tuple, list)):
        if len(spec) == 2:
            number, currency = spec[0], spec[1]
            return position.CostSpec(
                _as_decimal(number), None, currency, None, None, None
            )
        if len(spec) == 4:
            number, currency, date, label = spec
            return position.CostSpec(
                _as_decimal(number), None, currency, date, label, None
            )
    if isinstance(spec, dict):
        return position.CostSpec(
            number_per=_opt_decimal(spec.get("number_per", spec.get("number"))),
            number_total=_opt_decimal(spec.get("number_total", spec.get("total"))),
            currency=spec.get("currency"),
            date=spec.get("date"),
            label=spec.get("label"),
            merge=spec.get("merge"),
        )
    raise TypeError(f"unsupported cost spec: {spec!r}")


def _opt_decimal(value: object) -> Optional[Decimal]:
    return None if value is None else _as_decimal(value)


def _make_price(spec: Optional[Union[Tuple[object, str], Dict[str, object]]], units_number) -> Optional[amount.Amount]:
    """Normalize a price spec into a per-unit ``amount.Amount``.

    ``Posting.price`` is *always* per-unit in the data model, so a total price
    (``@@``) is stored after dividing by the posting's units. ``@`` is stored
    verbatim. Accepts ``(number, currency)``, ``("@total", number, currency)``,
    or ``{"total": ..., "currency": ...}``.
    """
    if spec is None:
        return None
    if isinstance(spec, amount.Amount):
        return spec
    number: Optional[object] = None
    currency: Optional[str] = None
    is_total = False
    if isinstance(spec, dict):
        currency = spec.get("currency")
        if "total" in spec:
            number = spec["total"]
            is_total = True
        else:
            number = spec.get("number")
    elif isinstance(spec, (tuple, list)):
        if len(spec) == 2:
            number, currency = spec[0], spec[1]
        elif len(spec) == 3 and spec[0] == "@@":
            currency = spec[2]
            number = spec[1]
            is_total = True
    else:
        return None
    if number is None or currency is None:
        raise ValueError(f"price spec needs a number and currency: {spec!r}")
    value = _as_decimal(number)
    if is_total:
        if not units_number or not _as_decimal(units_number):
            raise ValueError("total price needs non-zero units to split by")
        value = value / abs(_as_decimal(units_number))
    return amount.Amount(value, str(currency))


def make_posting(spec: PostingSpec) -> data.Posting:
    """Build a Posting from a spec dict or a shorthand tuple.

    Tuple shorthands (kept for ergonomics): ``(account, number, currency)``
    with an optional fourth cost; ``(account, None)`` for the elided balancing
    leg. Pass a dict for full control — every optional key is honoured:

      account   (str, required)  the account to post to
      number    (numeric|None)   units; None -> elided balancing leg
      currency  (str)            commodity of ``number``
      cost      (see _make_cost) acquisition cost or a lot-matching filter
      price     (see _make_price) per-unit price, or total price to split
      flag      (str)            per-posting flag overriding the txn flag
      meta      (dict)           per-posting Key: Value metadata
    """
    if isinstance(spec, dict):
        account = spec.get("account")
        number = spec.get("number")
        currency = spec.get("currency")
        units = None if number is None else amount.Amount(
            _as_decimal(number), str(currency)
        )
        cost = _make_cost(spec.get("cost"))
        price = _make_price(spec.get("price"), number)
        flag = spec.get("flag")
        meta = dict(spec.get("meta") or {})
        return data.Posting(account, units, cost, price, flag, meta or None)

    # Tuple shorthands: (account, number, currency[, cost, price, flag, meta]).
    account = spec[0]
    number = spec[1]
    cost: Optional[position.CostSpec] = None
    if number is not None:
        currency = spec[2]
        units = amount.Amount(_as_decimal(number), currency)
        if len(spec) >= 4 and spec[3] is not None:
            cost = _make_cost(spec[3])
    else:
        units = None
    price = _make_price(spec[4] if len(spec) >= 5 else None, number)
    flag = spec[5] if len(spec) >= 6 else None
    meta = spec[6] if len(spec) >= 7 else None
    return data.Posting(account, units, cost, price, flag, dict(meta) if meta else None)


def leg(
    account: str,
    number: object,
    currency: str,
    cost: object = None,
    price: object = None,
    flag: object = None,
    meta: object = None,
) -> Dict[str, object]:
    """Build a full posting-spec dict for one posted leg.

    Identical to the dict form accepted by :func:`make_posting` — every
    keyword maps to the same-named spec key, so optional ``cost`` /
    ``price`` / ``flag`` / ``meta`` are honoured when given. ``None``
    kwargs are omitted, leaving the posting without that attribute.
    """
    spec: Dict[str, object] = {"account": account, "number": number, "currency": currency}
    if cost is not None:
        spec["cost"] = cost
    if price is not None:
        spec["price"] = price
    if flag is not None:
        spec["flag"] = flag
    if meta is not None:
        spec["meta"] = meta
    return spec


def elided(account: str) -> Tuple[str, None]:
    """Build the elided balancing-leg spec for ``account``.

    Returns ``(account, None)`` — the opening of the transaction whose
    amount Beancount interpolates at load time so the transaction
    balances, instead of an explicit number here.
    """
    return (account, None)


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
    meta: Optional[Dict[str, object]] = None,
) -> data.Transaction:
    """Build a Beancount ``Transaction`` from plain dicts or tuples.

    Each posting is a full spec dict (see :func:`make_posting`) or a shorthand
    ``(account, number, currency[, cost])`` / ``(account, None)`` tuple. Pass
    ready-made ``data.Posting`` objects for full control. ``meta`` merges extra
    transaction-level ``Key: Value`` metadata beside the auto ``filename`` /
    ``lineno`` entries.
    """
    built: List[data.Posting] = []
    for posting in postings:
        if isinstance(posting, data.Posting):
            built.append(posting)
        else:
            built.append(make_posting(posting))
    txn_meta = data.new_metadata(filename, 0)
    if meta:
        txn_meta.update(meta)
    return data.Transaction(
        meta=txn_meta,
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
    meta: Optional[Dict[str, object]] = None,
) -> Tuple[data.Transaction, List[LedgerError]]:
    """Make a transaction in the yearly ledger file, validating first.

    The transaction is routed to ``ledgers/<YEAR>.bean`` (or the layout's
    yearly directory) when it exists, else to the ledger root. It is
    staged-validated before any append; on error the ledger is untouched.

    Args:
        path: The ledger root (a directory) or the ledger file itself,
            used to resolve where the transaction is written. The date
            selects the ``<YEAR>.bean`` file under the root when the
            layout has one; otherwise the transaction lands in the root.
            Only the path matters here — the file is opened in append mode
            and flushed, so the change is visible to the next staleness
            check on the same ``path``.
        date: A ``datetime.date`` for the transaction. Rendered as the
            directive's leading date line
            (``YYYY-MM-DD * "narration"``) and used to route the entry into
            the correct yearly ledger.
        narration: The required human description of the transaction,
            rendered on the directive row after the date and flag. This is
            what shows in a ledger listing (e.g. ``"Coffee at cafe"``).
        postings: The legs of the transaction — every account and amount
            moved. Each entry is either a plain ``data.Posting`` object or
            a spec:
              * dict ``{account, number, currency, cost, price, flag, meta}``
              * shorthand tuple ``(account, number, currency[, cost])``
              * elided leg ``(account, None)`` — left open to balance.
            A transaction must balance (sum of postings == 0) unless one
            leg is elided; validation catches a mismatch before writing.
        payee: Optional counterparty/merchant name, rendered second on the
            directive as ``… "narration"`` only when present. Often the merchant
            name, kept consistent so transactions group by who was paid
            (e.g. ``"Cafe Gato"``).
        flag: One-character Beancount flag marking the transaction's state
            (default ``"*"`` = normal/cleared). ``"!"`` marks a transaction
            needing attention. Used for reconciliation and dedup; override
            per-posting by a posting's own ``flag``.
        tags: An optional set of tag strings. Rendered on the directive as
            ``#tag1 #tag2`` and lets you group the transaction under one or
            more annotations for queries/reports.
        links: An optional set of link strings. Rendered as ``^link1
            ^link2`` and links this transaction to related ones (e.g. the
            same purchase split across accounts), enabling the ledger to
            connect them.
        meta: An optional dict of extra transaction-level ``Key: Value``
            metadata (e.g. ``{"id": "ord-1022"}``). Merged on top of the
            auto ``filename`` / ``lineno`` entries, so a key here
            overrides the auto one of the same name. Leaves are strings
            or numbers; multi-line strings become block strings.

    Returns:
        A tuple ``(txn, errors)``. ``txn`` is the built ``data.Transaction``
        (written to the ledger file only when ``errors`` is empty).
        ``errors`` is the list of loader errors from validating the
        prospective update: an empty list means it was appended and the
        ledger stays clean.
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
        meta=meta,
        filename=str(target),
    )
    rendered = format_transaction(txn)
    errors, include_line = validate_append(path, target, rendered)
    if not errors:
        append_transactions(target, [txn])
        if include_line is not None:
            append_include(path, include_line)
    return txn, errors
