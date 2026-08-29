"""Tests for transaction creation and appending utilities."""

from datetime import date
from decimal import Decimal

from beancount.core import amount, data, position

from ledger import add_transaction, append_transactions, format_transaction, make_transaction


def test_format_transaction_basic():
    txn = make_transaction(
        date(2024, 4, 1),
        "Coffee beans",
        [("Expenses:Food", "8.50", "USD"), ("Assets:Bank:Checking", None)],
        payee="Local Roastery",
        tags={"coffee"},
    )
    rendered = format_transaction(txn)
    assert '2024-04-01 * "Local Roastery" "Coffee beans" #coffee' in rendered
    assert "Expenses:Food         8.50 USD" in rendered
    assert "Assets:Bank:Checking" in rendered
    assert rendered.endswith("\n\n")


def test_make_transaction_balancing_leg_has_no_units():
    txn = make_transaction(
        date(2024, 4, 1),
        "Split",
        [
            ("Expenses:Food", "3.00", "USD"),
            ("Expenses:Home", "7.00", "USD"),
            ("Assets:Bank:Checking", None),
        ],
    )
    assert len(txn.postings) == 3
    assert txn.postings[2].units is None


def test_make_transaction_cost_posting():
    txn = make_transaction(
        date(2024, 4, 2),
        "Buy AAPL",
        [
            ("Assets:Broker", "5", "AAPL", ("182.00", "USD", None, None)),
            ("Assets:Bank:Checking", None),
        ],
    )
    cost = txn.postings[0].cost
    assert cost is not None
    assert isinstance(cost, position.CostSpec)
    assert cost.number_per == Decimal("182.00")
    assert cost.currency == "USD"
    rendered = format_transaction(txn)
    assert "5 AAPL {182.00 USD}" in rendered


def test_add_transaction_appends_and_stays_clean(scratch_ledger):
    txn, errors = add_transaction(
        scratch_ledger,
        date(2024, 12, 1),
        "New entry",
        [("Expenses:Food", "12.34", "USD"), ("Assets:Bank:Checking", None)],
    )
    assert errors == []
    assert isinstance(txn, data.Transaction)
    text = scratch_ledger.read_text()
    assert '2024-12-01 * "New entry"' in text
    assert "Expenses:Food         12.34 USD" in text


def test_append_transactions_many(scratch_ledger):
    txns = [
        make_transaction(date(2024, 11, 1), "One", [("Expenses:Food", "1.00", "USD"), ("Assets:Bank:Checking", None)]),
        make_transaction(date(2024, 11, 2), "Two", [("Expenses:Food", "2.00", "USD"), ("Assets:Bank:Checking", None)]),
    ]
    append_transactions(scratch_ledger, txns)
    text = scratch_ledger.read_text()
    assert '2024-11-01 * "One"' in text
    assert '2024-11-02 * "Two"' in text


def test_add_transaction_reports_breaking_append(tmp_path):
    """A balance assertion that the new entry breaks must surface as an error."""
    ledger = tmp_path / "b.bean"
    ledger.write_text(
        'option "operating_currency" "USD"\n'
        '1970-01-01 open Assets:Cash\n'
        '1970-01-01 open Expenses:Food\n'
        '2024-01-01 * "opening"\n'
        "  Assets:Cash    100.00 USD\n"
        "  Equity:Opening-Balances\n"
        '2024-02-01 balance Assets:Cash  100.00 USD\n'
    )
    _, errors = add_transaction(
        ledger,
        date(2024, 3, 1),
        "Spend",
        [("Expenses:Food", "10.00", "USD"), ("Assets:Cash", None)],
    )
    assert errors, "expected loader errors from the broken balance assertion"
    assert any("balance" in str(err["message"] or "").lower() for err in errors)


def test_make_transaction_accepts_posting_objects():
    posting = data.Posting("Assets:Cash", amount.Amount(Decimal("5"), "USD"), None, None, None, None)
    txn = make_transaction(
        date(2024, 4, 3), "Direct", [posting, ("Equity:Opening-Balances", None)]
    )
    assert txn.postings[0] is posting


def test_make_posting_per_unit_price():
    txn = make_transaction(
        date(2024, 4, 4),
        "Convert",
        [("Assets:Broker", "5", "AAPL", {"number": "182.00", "currency": "USD",
                                         "price": {"number": "197.90", "currency": "USD"}})],
    )
    posting = txn.postings[0]
    # Construct through the dict spec: cost and price both set.
    from ledger import make_posting
    p = make_posting({
        "account": "Assets:Broker",
        "number": "5",
        "currency": "AAPL",
        "cost": {"number": "182.00", "currency": "USD"},
        "price": {"number": "197.90", "currency": "USD"},
    })
    assert p.price == amount.Amount(Decimal("197.90"), "USD")
    assert isinstance(p.cost, position.CostSpec)
    assert p.cost.number_per == Decimal("182.00")
    assert "5 AAPL {182.00 USD} @ 197.90 USD" in format_transaction(
        make_transaction(date(2024, 4, 4), "capgains", [p])
    )


def test_make_posting_total_price_is_split_by_units():
    from ledger import make_posting
    p = make_posting({
        "account": "Assets:Cash",
        "number": "-436.00",
        "currency": "CAD",
        "price": {"total": "436.00", "currency": "USD"},
    })
    assert p.price == amount.Amount(Decimal("1.00"), "USD")
    # @@ total tuple shorthand
    p2 = make_posting({
        "account": "Assets:Cash",
        "number": "-100.00",
        "currency": "USD",
        "price": ("@@", "109.00", "CAD"),
    })
    assert p2.price == amount.Amount(Decimal("1.09"), "CAD")


def test_make_posting_cost_lot_filters():
    from ledger import make_posting
    p = make_posting({
        "account": "Assets:Broker",
        "number": "-5",
        "currency": "AAPL",
        "cost": {"date": date(2024, 12, 1)},
    })
    assert p.cost is not None
    assert isinstance(p.cost, position.CostSpec)
    assert p.cost.date == date(2024, 12, 1)
    assert p.cost.label is None
    p2 = make_posting({
        "account": "Assets:Broker",
        "number": "-5",
        "currency": "AAPL",
        "cost": {"label": "ref-001"},
    })
    assert p2.cost is not None
    assert isinstance(p2.cost, position.CostSpec)
    assert p2.cost.label == "ref-001"
    p3 = make_posting({
        "account": "Assets:Broker",
        "number": "-5",
        "currency": "AAPL",
        "cost": {},
    })
    assert isinstance(p3.cost, position.CostSpec)
    assert p3.cost.number_per is None and p3.cost.label is None and p3.cost.date is None


def test_make_posting_per_leg_flag_and_meta():
    from ledger import make_posting
    p = make_posting({
        "account": "Assets:Cash",
        "number": "-100",
        "currency": "USD",
        "flag": "!",
        "meta": {"reconciled": "true"},
    })
    assert p.flag == "!"
    assert p.meta == {"reconciled": "true"}


def test_make_transaction_merges_meta():
    txn = make_transaction(
        date(2024, 4, 5),
        "Meta",
        [("Expenses:Food", "4.00", "USD"), ("Assets:Bank:Checking", None)],
        meta={"txn_id": "A1", "approved": True},
    )
    assert txn.meta["txn_id"] == "A1"
    assert txn.meta["approved"] is True
    assert "txn_id: \"A1\"" in format_transaction(txn)


def test_leg_and_elided_build_full_spec():
    from ledger import elided, leg, make_transaction, format_transaction
    txn = make_transaction(
        date(2026, 1, 15),
        "Cafe Mogador",
        postings=[
            leg("Liabilities:CreditCard:CapitalOne", -37.45, "USD"),
            elided("Expenses:Restaurant"),
        ],
    )
    posted, open_ = txn.postings
    # leg: explicit amount, descend directly from the spec.
    assert posted.account == "Liabilities:CreditCard:CapitalOne"
    assert posted.units is not None
    assert posted.units.number == Decimal("-37.45")
    # elided: no units — the balance leg Beancount interpolates.
    assert open_.account == "Expenses:Restaurant"
    assert open_.units is None
    rendered = format_transaction(txn)
    assert "Liabilities:CreditCard:CapitalOne  -37.45 USD" in rendered


def test_leg_accepts_cost_price_flag_meta():
    from ledger import elided, leg, make_transaction
    txn = make_transaction(
        date(2024, 4, 6),
        "Buy AAPL",
        postings=[
            leg(
                "Assets:Broker", "5", "AAPL",
                cost={"number": "182.00", "currency": "USD"},
                price={"number": "197.90", "currency": "USD"},
                flag="!",
                meta={"note": "verified"},
            ),
            leg("Assets:Cash", "-910.00", "USD"),
        ],
    )
    posting = txn.postings[0]
    assert isinstance(posting.cost, position.CostSpec)
    assert posting.cost.number_per == Decimal("182.00")
    assert posting.price == amount.Amount(Decimal("197.90"), "USD")
    assert posting.flag == "!"
    assert posting.meta == {"note": "verified"}
