"""Tests for the receipt archive (receipts/<YEAR>.jsonl) and Receipt model."""

import json
import re
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from ledger import Receipt, ReceiptItem, add_receipt
from ledger.layout import KIND_RECEIPT, resolve_target


def _receipt(**overrides) -> Receipt:
    fields = {
        "currency": "EUR",
        "store_name": "Cafe Gato",
        "store_location": "Berlin",
        "items": [
            ReceiptItem(
                name="CAPPUCINO",
                name_inf="cappuccino",
                qty=1.0,
                unit="pcs",
                amount=3.5,
            )
        ],
    }
    fields.update(overrides)
    return Receipt(**fields)


def _receipts_dir(ledger_file: Path) -> Path:
    return ledger_file.parent / "receipts"


def test_resolve_target_routes_receipt_by_year(tmp_path):
    ledger_file = tmp_path / "ledger.bean"
    target = resolve_target(ledger_file, KIND_RECEIPT, date=date(2026, 3, 1))
    assert target == tmp_path / "receipts" / "2026.jsonl"


def test_add_receipt_creates_archive_and_returns_id(tmp_path):
    ledger_file = tmp_path / "ledger.bean"
    receipt_id = add_receipt(ledger_file, date(2026, 8, 21), _receipt())
    assert isinstance(receipt_id, str) and receipt_id
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-[0-9a-f]{8}", receipt_id), receipt_id
    archive = _receipts_dir(ledger_file) / "2026.jsonl"
    assert archive.exists()
    lines = archive.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    stored = json.loads(lines[0])
    assert stored == {
        "date": "2026-08-21",
        "currency": "EUR",
        "store_name": "Cafe Gato",
        "store_location": "Berlin",
        "items": [
            {
                "name": "CAPPUCINO",
                "name_inf": "cappuccino",
                "qty": 1.0,
                "unit": "pcs",
                "amount": 3.5,
            }
        ],
        "id": receipt_id,
    }


def test_add_receipt_appends_one_json_per_line(tmp_path):
    ledger_file = tmp_path / "ledger.bean"
    first = add_receipt(ledger_file, date(2026, 8, 21), _receipt(items=[]))
    second = add_receipt(ledger_file, date(2026, 8, 22), _receipt(items=[]))
    assert first != second
    lines = (_receipts_dir(ledger_file) / "2026.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["id"] == first
    assert json.loads(lines[1])["id"] == second


def test_add_receipt_routes_by_year(tmp_path):
    ledger_file = tmp_path / "ledger.bean"
    id_2025 = add_receipt(ledger_file, date(2025, 12, 31), _receipt(items=[]))
    id_2026 = add_receipt(ledger_file, date(2026, 1, 1), _receipt(items=[]))
    assert (_receipts_dir(ledger_file) / "2025.jsonl").exists()
    assert (_receipts_dir(ledger_file) / "2026.jsonl").exists()
    assert json.loads((_receipts_dir(ledger_file) / "2025.jsonl").read_text().splitlines()[0]) == {
        "date": "2025-12-31",
        "currency": "EUR",
        "store_name": "Cafe Gato",
        "store_location": "Berlin",
        "items": [],
        "id": id_2025,
    }
    assert json.loads((_receipts_dir(ledger_file) / "2026.jsonl").read_text().splitlines()[0]) == {
        "date": "2026-01-01",
        "currency": "EUR",
        "store_name": "Cafe Gato",
        "store_location": "Berlin",
        "items": [],
        "id": id_2026,
    }


def test_add_receipt_id_always_generated(tmp_path):
    """id lives on the model but must not be filled manually — ours wins."""
    ledger_file = tmp_path / "ledger.bean"
    receipt = _receipt(id="manually-set", items=[])
    assert receipt.id == "manually-set"
    receipt_id = add_receipt(ledger_file, date(2026, 8, 21), receipt)
    stored = json.loads((_receipts_dir(ledger_file) / "2026.jsonl").read_text().splitlines()[0])
    assert list(stored) == ["date", "currency", "store_name", "store_location", "items", "id"]
    assert stored["id"] == receipt_id
    assert stored["id"] != "manually-set"


def test_receipt_id_defaults_none():
    receipt = _receipt()
    assert receipt.id is None


def test_add_receipt_keeps_model_untouched(tmp_path):
    ledger_file = tmp_path / "ledger.bean"
    receipt = _receipt()
    add_receipt(ledger_file, date(2026, 8, 21), receipt)
    assert receipt.currency == "EUR"
    assert receipt.items == [
        ReceiptItem(name="CAPPUCINO", name_inf="cappuccino", qty=1.0, unit="pcs", amount=3.5)
    ]


def test_add_receipt_optional_store_fields(tmp_path):
    ledger_file = tmp_path / "ledger.bean"
    receipt_id = add_receipt(ledger_file, date(2026, 8, 21), _receipt(store_name=None, store_location=None, items=[]))
    stored = json.loads((_receipts_dir(ledger_file) / "2026.jsonl").read_text().splitlines()[0])
    assert stored["store_name"] is None
    assert stored["store_location"] is None
    assert stored["id"] == receipt_id


def test_receipt_requires_its_schema(tmp_path):
    with pytest.raises(ValidationError):
        Receipt(currency="USD")  # type: ignore[call-arg]  # missing required fields on purpose
    with pytest.raises(ValidationError):
        _receipt(items=[{"name": "no-amount"}])  # ReceiptItem.amount is required
    with pytest.raises(ValidationError):
        Receipt(currency="USD", store_name="X", store_location=None, items=[ReceiptItem(name="a", amount="not-a-number")])  # type: ignore[arg-type]  # invalid value on purpose


def test_receipt_item_defaults_qty_and_optional_fields():
    item = ReceiptItem(name="coffee", amount=3.5)
    assert item.qty == 1.0
    assert item.name_inf is None
    assert item.unit == "pcs"