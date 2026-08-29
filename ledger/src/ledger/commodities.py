"""Create and append Beancount v3 commodity directives.

Build `beancount.core.data.Commodity` objects (`1970-01-01 commodity
AAPL`), format them like a hand-written ledger, and append them to the
ledger file. The resulting file is re-validated so you never silently
write a broken ledger.
"""

import io
from datetime import date as _date
from pathlib import Path
from typing import Iterable, List, Tuple, Union

from beancount.core import data
from beancount.parser import printer

from .layout import KIND_COMMODITY, append_include, resolve_target, validate_append
from .ledger import LedgerError


def make_commodity(
    date: _date,
    currency: str,
    *,
    filename: str = "<commodity>",
) -> data.Commodity:
    """Build a Beancount ``Commodity`` directive declaring ``currency``."""
    return data.Commodity(
        meta=data.new_metadata(filename, 0),
        date=date,
        currency=currency,
    )


def format_commodity(commodity: data.Commodity) -> str:
    """Render a Commodity exactly as it would appear in a ledger file.

    Includes the trailing blank line Beancount uses between directives.
    """
    buf = io.StringIO()
    printer.print_entry(commodity, file=buf)
    return buf.getvalue()


def append_commodities(
    path: Union[str, Path], commodities: Iterable[data.Commodity]
) -> None:
    """Append formatted commodity directives to the ledger file.

    The file is opened in append mode and flushed, so a change is visible
    to the next ``LedgerManager`` staleness check.
    """
    path = Path(path)
    rendered = "".join(format_commodity(c) for c in commodities)
    with path.open("a", encoding="utf-8") as f:
        f.write(rendered)
        f.flush()


def add_commodity(
    path: Union[str, Path],
    date: _date,
    currency: str,
) -> Tuple[data.Commodity, List[LedgerError]]:
    """Declare a commodity in the commodities split file, validating first.

    The directive is routed to ``commodities.bean`` (next to the root) when
    the layout splits it out, staged-validated before writing, and only
    appended once clean. Returns ``(commodity, errors)``; an empty errors
    list means it was written cleanly.
    """
    target = resolve_target(path, KIND_COMMODITY)
    commodity = make_commodity(date, currency, filename=str(target))
    rendered = format_commodity(commodity)
    errors, include_line = validate_append(path, target, rendered)
    if not errors:
        append_commodities(target, [commodity])
        if include_line is not None:
            append_include(path, include_line)
    return commodity, errors
