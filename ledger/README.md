# ledger — Beancount v3 accounting server

A Python server that exposes a [Beancount](https://beancount.github.io/)
v3 ledger as validated read/write tools for LLM agents and chat clients.
The primary interface is [MCP](https://modelcontextprotocol.io/) (stdio +
HTTP/SSE) — editors, Claude Desktop, and any MCP client speak to it — and
a Telegram bot fronts the same tools from any device. See the
[root README](../README.md) for the whole system (beancount + git
backend, `ledgerd` sync, deployment).

The server has four layers:

- **MCP server** (`ledger/mcp_server.py`) — exposes 11 tools over two
  transports: stdio (`uv run mcp_stdio`) and HTTP/SSE (`uv run
  mcp_server`, endpoint `http://127.0.0.1:8000/mcp`).
- **Telegram bot** (`src/tg_bot`) — the chat front-end; runs the same
  tools and summarizes what it did.
- **Core** (`ledger/ledger.py` + write bindings) — `LedgerManager`, a
  cached [beanquery](https://github.com/beancount/beanquery) connection
  with mtime-based staleness checks and `bean-check` validation, plus
  bindings that build and append Beancount directives of every type
  (transactions, accounts, commodities, the rest). Every append is
  re-validated before it touches disk, so the server never writes a
  broken ledger.
- **Query helpers** (`ledger/queries.py`) — BQL helpers that return
  [pydantic](https://docs.pydantic.dev/) models: orientation, paged
  `run_query`, tables, accounts, commodities, prices.

Tool definitions are single-sourced in `mcp_tools/`; `tools.py` bridges
them to the `chat` client library so one registry drives MCP, the bot, and
any chat loop.

A runnable example ledger and playground script live in
[`examples/`](examples/main.bean).

## Install

```sh
uv sync            # or: pip install -e .
```

Requires Python ≥ 3.13. Dependencies: `beancount>=3.2.3` and
`beanquery>=0.1`.

## Quick start

Play with the whole API against a throwaway copy of the example ledger
(read demos run against the real one, write demos against a temp copy —
the shipped files are never mutated):

```sh
cd ledger
uv sync --frozen --dev
uv run python examples/example.py            # or: examples/example.py some.bean
uv run pytest        # 127 tests
```

## The server: MCP + Telegram

The 11 registered tools operate on the canonical ledger given by
`LEDGER_PATH` (see `ledger/globals.py`); no tool takes a path. Every write
is staged and validated against the whole tree before persisting — on
error the ledger is untouched and the errors are returned.

```sh
# MCP stdio — register with an editor, Claude Desktop, `mcp` CLI, ...
export LEDGER_PATH=/path/to/main.bean
uv run mcp_stdio

# MCP HTTP/SSE — endpoint http://127.0.0.1:8000/mcp
uv run mcp_server

# Telegram bot and/or the HTTP server in one process (shared event loop)
uv run serve tg          # bot only
uv run serve mcp         # HTTP/SSE only
uv run serve tg mcp      # both
```

Bot env: `LEDGER_TG_BOT_TOKEN`, `LEDGER_TG_TARGET_USER_ID`,
`LEDGER_OPENROUTER_API_KEY` (model: `openrouter/google/gemini-3.7-flash`).
HTTP host/port: `LEDGER_MCP_HOST` / `LEDGER_MCP_PORT` (defaults
`127.0.0.1:8000`); the SSE transport is session-based (`GET /mcp` opens a
stream and returns an `Mcp-Session-Id`, `POST /mcp` sends JSON-RPC to it,
`DELETE` closes it).

The tools:

- **`run_query`** — any BQL statement, read-only; returns a TSV table
  capped at 200 rows with an `offset` paging note. The tool description
  carries the full BQL grammar (SELECT/JOURNAL/BALANCES/PRINT, FROM/OPEN
  ON/CLOSE ON/CLEAR, position functions, examples).
- **`add_transaction`** — append a validated transaction; auto-routed to
  `ledgers/<YEAR>.bean` and tagged `#agent`. Postings use flat
  `cost_*`/`price_*` keys and `elided: true` for the balancing leg.
- **`list_accounts`** / **`list_balances`** — read-only; confirm exact
  account names/balances before posting. Fails closed if the ledger has
  loader errors.
- **`add_account`** — open an account (currencies, booking).
- **`add_receipt`** / **`get_receipts_by_ids`** — archive purchase
  evidence as JSON in `receipts/<YEAR>.jsonl` (id `YYYY-MM-DD-<hex>`,
  generated server-side); link receipts to transactions via `receipt_ids`
  meta.
- **`add_recurring`** / **`list_recurring`** / **`update_recurring`** /
  **`delete_recurring`** — manage recurring transactions.

Verify the stdio server end-to-end without a client:

```sh
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | uv run mcp_stdio
```

> Reads never see a stale ledger: `LedgerManager` fingerprints the root
> file *and everything it includes* (mtime_ns, size) on each access and
> reloads on mismatch. Writes flush before returning, so the next access
> observes them.

---

## Core: `LedgerManager`

Every binding takes either a `LedgerManager` (query helpers) or a ledger
file path (write bindings).

```python
from pathlib import Path
from ledger import LedgerManager

mgr = LedgerManager(Path("first.bean"))
```

### Methods

| Method | Signature | Behavior |
|---|---|---|
| `connection()` | `() -> beanquery.Connection` | Cached beanquery connection; reloads when a stale fingerprint is seen (thread-safe). |
| `connection_errors()` | `() -> List[LedgerError]` | Structured loader/validation errors; empty means clean. |
| `check()` | `() -> List[LedgerError]` | Full re-parse with `beancount.loader` (ignores the cache). |
| `invalidate()` | `() -> None` | Force the next `connection()` to reload. |

### `LedgerError`

A loader/validation error is a plain dict:

```python
{
    "file": "first.bean",     # Optional[str]
    "line": 3,                # Optional[int]
    "type": "ValidationError",# str — error class name
    "message": "...",         # str
}
```

`LedgerError = Dict[str, object]`; `FileState = Dict[Path, Optional[Tuple[int, int]]]`.

---

## Querying the ledger

### Raw BQL through the connection

```python
conn = mgr.connection()
cursor = conn.execute(
    "SELECT account, sum(position) GROUP BY account ORDER BY account"
)
for account, balance in cursor.fetchall():
    print(f"{account:30} {balance}")
```

BQL is SQL-like with two caveats: `FROM` is a date/filter clause, not a
table selector; multi-currency sums return an `Inventory` — wrap with
`convert()` for one currency:

```sql
SELECT convert(sum(position), 'USD') WHERE account ~ 'Assets|Liabilities'
```

### `run_query` — paged BQL results

```python
from ledger import run_query

out = run_query(mgr, "SELECT account, sum(position) GROUP BY account ORDER BY account")
# out.columns        -> ['account', 'sum(position)']
# out.rows           -> [['Assets:Bank:Checking', '(-320.45 USD)'], ...]  (strings)
# out.truncated      -> False    out.offset -> 0    out.total_rows -> 7
page2 = run_query(mgr, "SELECT date, narration WHERE account ~ 'Assets'", offset=200)
```

Rows are capped at 200 (`ROW_LIMIT`); pass `offset` to page. A
syntactically invalid query returns the model with `error_type="bql"` and
the message in `error` — it does not raise.

### High-level helpers

All take the `LedgerManager` and return a pydantic model; on failure the
`error` / `error_type` fields are set instead (see
[Result models](#result-models)).

```python
from ledger import (
    ledger_info, date_range, bean_check,
    account_names, list_accounts,
    commodity_names, list_commodities,
    table_names, list_tables, list_prices,
)

info = ledger_info(mgr)
# info.operating_currency -> "USD"   info.title -> "Sample Ledger"
# info.date_range -> DateRange(first=..., last=...)   info.account_count -> 7

span = date_range(mgr)          # dates with at least one posting

out = bean_check(mgr)           # out.ok -> True; out.message -> "Ledger is clean — no errors or warnings."

account_names(mgr)              # sorted; no 200-row cap on list_* helpers
prices = list_prices(mgr)       # latest price per commodity (last-in-file wins per day)
```

`commodity_names` unions declared commodities, the `prices` table, and
every currency appearing in a posting.

---

## Writing directives

### The `make_/format_/append_/add_` convention

Every write binding follows the same four-function shape:

- **`make_<type>(...)`** — build the `beancount.core.data` object from
  plain Python values (amounts coerced with `Decimal(str(...))`).
- **`format_<type>(entry)`** — render it exactly like a hand-written
  ledger file.
- **`append_<type>s(path, entries)`** — append (and flush) rendered
  directives; never validates.
- **`add_<type>(path, ...)`** — make one, append it, and validate.
  Returns `(entry, errors)`. **Validation happens *before* any write:
  on error nothing is modified.** The directive is routed to the
  canonical layout: `open`/`close` → `accounts.*`, `commodity`/`price` →
  `commodities.*`, other dated directives → `ledgers/<YEAR>.*` (falling
  back to `path` for single-file ledgers). A transaction routed to a year
  file the root does not include yet also gets one `include` line
  appended to the root.

### Transactions (`ledger/transactions.py`)

```python
from datetime import date
from ledger import make_transaction, add_transaction, format_transaction

txn = make_transaction(
    date(2024, 2, 1),
    "Coffee beans",
    [
        ("Expenses:Food", "8.50", "USD"),
        ("Assets:Cash", None),            # balancing leg
    ],
    payee="Local Roastery",
    tags={"coffee"},
)
print(format_transaction(txn))
# 2024-02-01 * "Local Roastery" "Coffee beans" #coffee
#   Expenses:Food         8.50 USD
#   Assets:Cash
```

`make_transaction(date, narration, postings, *, payee=None, flag="*",
tags=None, links=None, filename="<transaction>")`. `postings` accepts
ready-made `data.Posting` objects or tuples:

| Posting spec | Meaning |
|---|---|
| `(account, number, currency)` | posted amount |
| `(account, None)` | balancing leg — amount computed by Beancount |
| `(account, number, currency, cost)` | lot-tracked; `cost` is `(number, currency)` or `(number, currency, date, label)` |

```python
make_transaction(date(2024, 2, 1), "Buy AAPL", [
    ("Assets:Broker", "5", "AAPL", ("182.00", "USD")),
    ("Assets:Cash", None),
])
#   Assets:Broker       5 AAPL {182.00 USD}
#   Assets:Cash
```

Also exported: `make_posting(spec)`, `append_transactions(path, txns)`,
`PostingSpec` / `CostSpec`.

### Accounts (`ledger/accounts.py`)

```python
from ledger import add_account, close_account

open_, errors = add_account(
    Path("first.bean"), date(2024, 1, 1), "Assets:Broker",
    currencies=["USD", "AAPL"], booking="FIFO",
)   # booking: None | data.Booking | "FIFO" / "STRICT" (case-insensitive)
close, errors = close_account(Path("first.bean"), date(2024, 12, 31), "Assets:Broker")
```

Builders/formatters: `make_open(date, account, *, currencies=None,
booking=None, filename="<open>")`, `make_close(date, account)`,
`format_account(entry)`, `append_accounts(path, entries)`; `AccountEntry
= Union[Open, Close]`.

### Commodities (`ledger/commodities.py`)

```python
from ledger import add_commodity

commodity, errors = add_commodity(Path("first.bean"), date(2024, 9, 1), "BTC")
```

Builders/formatters: `make_commodity(date, currency, *, filename="<commodity>")`,
`format_commodity`, `append_commodities`.

### The remaining directive types (`ledger/directives.py`)

| Type | `make_<type>(...)` | `add_<type>(path, ...) -> (entry, errors)` |
|---|---|---|
| `balance` | `(date, account, number, currency, *, tolerance=None)` | `(path, date, account, number, currency, *, tolerance=None)` |
| `pad` | `(date, account, source_account)` | `(path, date, account, source_account)` |
| `note` | `(date, account, comment)` | `(path, date, account, comment)` |
| `document` | `(date, account, path)` | `(path, date, account, doc_path)` |
| `price` | `(date, currency, number, quote_currency)` | `(path, date, currency, number, quote_currency)` |
| `event` | `(date, type, description)` | `(path, date, type, description)` |
| `query` | `(date, name, query_string)` | `(path, date, name, query_string)` |
| `custom` | `(date, type, values=None)` | `(path, date, type, values=None)` |

`custom` values coerce to `ValueType(value, dtype)`: `bool`/`int`/`float`/
`Decimal` → `Decimal`, `date` → `date`, `Amount` → `amount`, `str` → `str`.

Validation follows Beancount's own rules, surfaced via `errors`:
`add_pad` alone is rejected ("unused pad") unless a later balance
assertion needs it; `add_balance` asserts against the ledger's real
balance at that date; `add_document` requires the file to exist.

---

## Result models (`ledger.models`)

Every query helper returns one of these pydantic models; all inherit
`Base` (error fields). Serialize with `.model_dump()` /
`.model_dump_json()`.

| Model | Fields | Returned by |
|---|---|---|
| `Base` | `error: Optional[str]`, `error_type: Optional[str]`, `errors: List[LedgerIssue]` | — (base class) |
| `LedgerIssue` | `file`, `line`, `type`, `message` (all Optional[str/int]) | `bean_check`, error paths |
| `DateRange` | `first: Optional[str]`, `last: Optional[str]` (ISO) | `date_range`, `ledger_info.date_range` |
| `LedgerInfo` | `today`, `title`, `operating_currency`, `date_range`, `account_count`, `account_roots` | `ledger_info` |
| `QueryResult` | `columns`, `rows`, `truncated`, `returned_rows`, `offset`, `total_rows`, `total_rows_known` | `run_query` |
| `CheckResult` | `ok: bool`, `message: str` | `bean_check` |
| `AccountsList` | `accounts`, `count` | `list_accounts` |
| `CommoditiesList` | `commodities`, `count` | `list_commodities` |
| `TablesList` | `tables`, `warning` | `list_tables` |
| `Price` | `commodity`, `date`, `price` | `list_prices` |
| `PricesList` | `prices`, `count` | `list_prices` |

Error handling: `error_type="bql"` — invalid query/parameter (no raise);
`error_type="ledger"` — the ledger itself has errors (`errors` repeats the
issues). `QueryResult.total_rows` is exact only when `truncated` is
`False`.

---

## Example ledger and playground

[`examples/`](examples/main.bean) is a small multi-currency ledger laid
out like a real one: `main.bean` (options + includes + transactions + a
price), `accounts.bean` (open directives), `commodities.bean`
(declarations) — the same split the write bindings' layout routing uses.
`example.py` demos the whole API: orientation, accounts, commodities,
prices, tables, a BQL query, then each write binding appended to a
throwaway copy with its loader-error count.

```sh
uv run python examples/example.py            # the examples/main.bean ledger
uv run python examples/example.py some.bean  # or any ledger you point at
```