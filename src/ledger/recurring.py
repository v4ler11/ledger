"""Recurring payment rules: a ``recurring.json`` store next to the ledger.

Rules live in ``recurring.json`` in the same directory as the ledger root
(beside ``accounts.bean`` and any split files), written with ``indent=4``.
The module is the single source of truth for the store's CRUD and for the
lazy settlement sweep:

* ``add`` / ``update`` / ``delete`` / ``list_rules`` — basic operations.
* ``process_recurring`` — settle every due rule as a transaction.
* ``recurring_check`` — the throttled (≈hourly) entry point the MCP
    middleware calls before each tool.

Settlement is lazy: nothing is watched; ``recurring_check`` is invoked by
the server's middleware whenever any tool runs, and does real work only
when some ``next_settle_date`` is strictly before today and the hourly
throttle window has elapsed. Each settled rule advances its
``next_settle_date`` via the next rrule occurrence, so a rule never pays
twice for the same period.
"""

import json
import time as _clock
from datetime import date, datetime, time
from pathlib import Path
from typing import Dict, List, Literal, Optional, Union

from dateutil.rrule import rrulestr
from pydantic import BaseModel, Field

from .ledger import LedgerError
from .transactions import add_transaction, elided, leg

# Throttle: settle due rules at most once per this many seconds.
HOUR_SECONDS = 3600.0


class RecurringRule(BaseModel):
    """One scheduled payment, settled into the ledger on its due date."""

    id: str
    payee: str
    narration: str = ""
    account_from: str          # funding/source account, e.g. "Assets:Wise:USD"
    account_to: str            # expense/income account, e.g. "Expenses:Software"
    amount: float
    currency: str
    rrule: str = Field(
        ..., description="RFC5545 recurrence string, e.g. FREQ=MONTHLY;BYMONTHDAY=5"
    )
    next_settle_date: date
    status: Literal["active", "paused"] = "active"


class RecurringStore(BaseModel):
    rules: dict[str, RecurringRule] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Persistence (recurring.json, indent=4)
# ---------------------------------------------------------------------------


def store_path(ledger: Union[str, Path]) -> Path:
    """Path of recurring.json — next to the ledger root (accounts.bean)."""
    return Path(ledger).expanduser().resolve().parent / "recurring.json"


def load_rules(ledger: Union[str, Path]) -> RecurringStore:
    """Read the store from disk; an empty store when the file is absent."""
    path = store_path(ledger)
    if not path.exists():
        return RecurringStore()
    raw = json.loads(path.read_text(encoding="utf-8"))
    return RecurringStore.model_validate(raw)


def save_rules(ledger: Union[str, Path], store: RecurringStore) -> None:
    """Persist the store to recurring.json, formatted with indent=4."""
    text = json.dumps(store.model_dump(mode="json"), indent=4, ensure_ascii=False) + "\n"
    store_path(ledger).write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------


def add_rule(ledger: Union[str, Path], rule: RecurringRule) -> RecurringRule:
    """Add a rule; rejects an id that already exists."""
    store = load_rules(ledger)
    if rule.id in store.rules:
        raise ValueError(f"recurring rule {rule.id!r} already exists")
    store.rules[rule.id] = rule
    save_rules(ledger, store)
    return rule


def update_rule(ledger: Union[str, Path], rule_id: str, **changes: object) -> RecurringRule:
    """Patch an existing rule (any subset of its fields) and return it."""
    store = load_rules(ledger)
    rule = store.rules.get(rule_id)
    if rule is None:
        raise KeyError(f"no recurring rule {rule_id!r}")
    updated = rule.model_copy(update=changes)
    store.rules[rule_id] = updated
    save_rules(ledger, store)
    return updated


def delete_rule(ledger: Union[str, Path], rule_id: str) -> RecurringRule:
    """Remove a rule and return what was removed."""
    store = load_rules(ledger)
    removed = store.rules.pop(rule_id, None)
    if removed is None:
        raise KeyError(f"no recurring rule {rule_id!r}")
    save_rules(ledger, store)
    return removed


def list_rules(ledger: Union[str, Path]) -> List[RecurringRule]:
    """Return all rules sorted by id."""
    return sorted(load_rules(ledger).rules.values(), key=lambda r: r.id)


def format_rule(rule: RecurringRule) -> str:
    """Render one rule as a human-readable one-liner."""
    title = rule.narration or rule.payee or rule.id
    status = f" ({rule.status})" if rule.status != "active" else ""
    return (
        f"{rule.next_settle_date.isoformat()}  {title}  "
        f"{_fmt_amount(rule.amount)} {rule.currency}  "
        f"{rule.account_from} -> {rule.account_to}  #{rule.id}{status}"
    )


def _fmt_amount(value: float) -> str:
    text = f"{value:.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------


def process_recurring(
    ledger: Union[str, Path], today: Optional[date] = None
) -> List[Dict[str, object]]:
    """Settle every active rule whose next_settle_date is before ``today``.

    Each due rule is written to the ledger dated on its next_settle_date,
    then advanced to the next rrule occurrence and persisted. A rule whose
    settlement fails validation (e.g. a target account is not opened) is
    left due and reported with ``ok: False`` so a later check retries it.

    Returns one outcome dict per checked rule: either
    ``{id, ok: True, date, next}`` or ``{id, ok: False, errors}``.
    """
    today = today or date.today()
    store = load_rules(ledger)
    outcomes: List[Dict[str, object]] = []
    changed = False
    for rule in list(store.rules.values()):
        if rule.status != "active":
            continue
        if today <= rule.next_settle_date:
            continue
        errors = _settle(ledger, rule)
        if errors:
            outcomes.append({"id": rule.id, "ok": False, "errors": errors})
            continue
        rule.next_settle_date = _next_occurrence(rule)
        changed = True
        outcomes.append(
            {
                "id": rule.id,
                "ok": True,
                "date": today.isoformat(),
                "next": rule.next_settle_date.isoformat(),
            }
        )
    if changed:
        save_rules(ledger, store)
    return outcomes


def _settle(ledger: Union[str, Path], rule: RecurringRule) -> List[LedgerError]:
    """Write one rule's payment as a ledger transaction.

    The transaction is dated on the rule's due date (next_settle_date),
    tagged with its id in the ``recurring-id`` meta, and posts the amount
    into ``account_to`` with ``account_from`` as the elided balancing leg.
    """
    _, errors = add_transaction(
        ledger,
        rule.next_settle_date,
        rule.narration or rule.payee,
        [leg(rule.account_to, rule.amount, rule.currency), elided(rule.account_from)],
        payee=rule.payee,
        meta={"recurring-id": rule.id},
    )
    return errors


def _next_occurrence(rule: RecurringRule) -> date:
    """The date of the rule's next recurring settle after its due date."""
    anchor = datetime.combine(rule.next_settle_date, time.min)
    try:
        recurrence = rrulestr(rule.rrule, dtstart=anchor)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid rrule {rule.rrule!r} for {rule.id!r}: {exc}")
    following = recurrence.after(anchor)
    if following is None:
        raise ValueError(f"rrule {rule.rrule!r} for {rule.id!r} never recurs")
    return following.date()


# ---------------------------------------------------------------------------
# Throttled entry point used by the MCP middleware
# ---------------------------------------------------------------------------

_last_check: float = 0.0


def recurring_check(
    ledger: Union[str, Path], today: Optional[date] = None
) -> List[Dict[str, object]]:
    """Settle due rules, at most once per hour.

    Cheap when nothing is due or when the throttle window has not elapsed:
    returns ``[]`` in both cases. The middleware calls this before every
    tool; the per-hour cadence keeps the sweep from running hot on every
    request.
    """
    global _last_check
    now = _clock.monotonic()
    if now - _last_check < HOUR_SECONDS:
        return []
    _last_check = now
    return process_recurring(ledger, today=today)