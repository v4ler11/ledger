"""Pydantic result structures for the ledger query helpers.

Every query helper returns one of these models. On success the normal
fields are populated; on failure — an invalid ledger, a bad BQL query, a
malformed date, or a missing operating currency — the ``error`` /
``error_type`` fields are set instead (``errors`` repeats the individual
loader issues when the ledger itself is broken). Check ``result.error``
before reading the other fields.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class LedgerIssue(BaseModel):
    """A single loader/validation error from the ledger."""

    file: Optional[str] = None
    line: Optional[int] = None
    type: Optional[str] = None
    message: Optional[str] = None


class Base(BaseModel):
    """Common error-carrying fields for every result."""

    error: Optional[str] = None
    error_type: Optional[str] = None
    errors: List[LedgerIssue] = Field(default_factory=list)


class DateRange(Base):
    first: Optional[str] = None
    last: Optional[str] = None


class LedgerInfo(Base):
    today: str = ""
    title: Optional[str] = None
    operating_currency: Optional[str] = None
    date_range: DateRange = DateRange()
    account_count: int = 0
    account_roots: List[str] = Field(default_factory=list)


class QueryResult(Base):
    columns: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)
    truncated: bool = False
    returned_rows: int = 0
    offset: int = 0
    total_rows: Optional[int] = None
    total_rows_known: bool = False


class CheckResult(Base):
    ok: bool = False
    message: str = ""


class AccountsList(Base):
    accounts: List[str] = Field(default_factory=list)
    count: int = 0


class CommoditiesList(Base):
    commodities: List[str] = Field(default_factory=list)
    count: int = 0


class TablesList(Base):
    tables: List[str] = Field(default_factory=list)
    warning: str = ""


class Price(BaseModel):
    commodity: str = ""
    date: str = ""
    price: str = ""


class PricesList(Base):
    prices: List[Price] = Field(default_factory=list)
    count: int = 0
