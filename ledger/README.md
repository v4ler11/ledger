# ledger — Beancount v3 library

Pure Python bindings for working with [Beancount](https://beancount.github.io/)
v3 ledgers — a library you embed in your own programs, plus an optional
stdio [MCP](https://modelcontextprotocol.io/) server exposing the write
bindings as tools (see [MCP server](#mcp-server)).

The library has four parts:

- **`LedgerManager`** (`ledger/ledger.py`) — a cached
  [beanquery](https://github.com/beancount/beanquery) connection with
  mtime-based staleness checks and `bean-check` validation.
- **Write bindings** — build and append Beancount directives of every
  type: transactions (`ledger/transactions.py`), accounts
  (`ledger/accounts.py`), commodities (`ledger/commodities.py`), and the
  remaining directive types (`ledger/directives.py`). Every append is
  re-validated so you never silently write a broken ledger.
- **Query helpers** (`ledger/queries.py`) — basic BQL helpers that
  return [pydantic](https://docs.pydantic.dev/) models: orientation,
  paged `run_query`, tables, accounts, commodities, and prices.
- **MCP server** (`ledger/mcp_server.py`) — a stdio JSON-RPC server that
  exposes the write and read bindings to an MCP client; `add_transaction`
  and `list_accounts`.
- **Chat bridge** (`ledger/tools.py`) — adapts the same tools to the
  `chat` library's `Tool` protocol, so one set of definitions drives both
  MCP and an OpenAI-style chat loop (see [Chat bridge](#chat-bridge)).

A runnable example ledger and a playground script live in
[`examples/`](examples/main.bean).

## Install

```sh
uv sync            # or: pip install -e .
```

Requires Python ≥ 3.13. Dependencies: `beancount>=3.2.3` and
`beanquery>=0.1`.

## MCP server

`ledger/mcp_server.py` is an MCP server speaking JSON-RPC 2.0 over stdio: it
reads one request per line on stdin and answers on stdout, so any MCP
client can launch it as a subprocess. Run it from the project directory:

```sh
uv run mcp_server
```

or via the project venv's console script (an absolute path some clients
prefer):

```sh
/Users/valerii/code/ledger/ledger/.venv/bin/mcp_server
```

Register one of those commands with an MCP client (editor, Claude Desktop,
`mcp` CLI, …) as a *stdio* server — no ports, no daemon. The client spawns
the command fresh on every session and exchanges JSON-RPC over the pipes.
`uv run mcp_server` resolves from the project root, so point the client's
command at the `ledger/` directory if the client doesn't inherit one.
Verify it end-to-end without a client:

```sh
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | uv run mcp_server
# {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "add_transaction", ...}, {"name": "list_accounts", ...}]}}
```

Execute `add_transaction` the same way — one JSON-RPC request per line:

```sh
echo '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"add_transaction","arguments":{"date":"2026-08-19","narration":"Top up Wise","payee":"Mono","postings":[{"account":"Assets:Mono:EUR","number":"-150.00","currency":"EUR"},{"account":"Assets:Wise:EUR","elided":true}]}}}' \
  | uv run mcp_server
```

The result echoes the rendered directive and, in `structuredContent`,
the posted accounts plus `errors: []` when the ledger stayed clean. A
transaction is routed to the matching yearly file under the root
(`ledgers/<YEAR>.bean`) and auto-tagged `#agent`.

The registered tools:

- **`add_transaction`** — append a validated Beancount transaction to the
  ledger. Staged and validated before anything writes; on error the
  ledger is untouched and errors are returned. Postings use flat
  `cost_*`/`price_*` keys and `elided: true` for the auto-balancing
  leg — examples are in the tool's description.
- **`list_accounts`** — list every account declared in the ledger,
  sorted. Read-only. Use it to confirm exact account names before
  posting. Fails closed: a ledger with loader errors returns them
  instead of a partial list.

Neither tool takes a path — both operate on the canonical ledger, taken
from the required `LEDGER_PATH` environment variable
(`src/ledger/globals.py`). The Docker compose files set it to the ledger
file inside the synced repo checkout (see below).

### Docker client (reader + MCP over stdio)

[`docker-compose.client.yml`](../docker-compose.client.yml) (prod,
prebuilt images) and `docker-compose.client.dev.yml` (dev, local builds)
pair a read-only `reader` (ledgerd `Dockerfile.reader`) that keeps a
mirror of the finances repo on disk with a `ledger` service hosting the
MCP server.

The `ledger` service has **no baked command** — the image ships without a
CMD, and stdio MCP means the user executes the server fresh on every
session and hands the exec command to his chat client. The client sends
JSON-RPC requests on the process's stdin and reads replies from stdout;
nothing runs in the background.

Keep the reader up as a daemon:

```sh
# prod (prebuilt images) / dev (local builds)
docker compose -f docker-compose.client.yml up -d reader
docker compose -f docker-compose.client.dev.yml up -d reader --build
```

Then register the MCP server with the chat client — either host-side
(from `ledger/`), or containerized over the reader's checkout
(`./data/reader-repo`, `LEDGER_PATH=/data/main.bean`; `-T` keeps stdio a
pipe, not a TTY):

```sh
# host-side — every session runs the command fresh
uv run mcp_server
# or absolute, cwd-independent
/Users/valerii/code/ledger/ledger/.venv/bin/mcp_server

# containerized — spawns a one-off ledger container (waits for the
# reader's first fetch via service_healthy)
docker compose -f /path/to/docker-compose.client.yml run --rm -T ledger uv run mcp_server
```

The reader never pushes; it hard-resets to `origin/$BRANCH` every
`PULL_INTERVAL` and needs an SSH key with read access (see
[ledgerd/README.md](../ledgerd/README.md)).

## Chat bridge

The same tools are available to chat loops: `ledger.tools` adapts the
MCP definitions and handlers from `ledger/mcp_server.py` to the `chat`
library's `Tool` protocol — names, descriptions, and JSON schemas are
single-sourced in `mcp_server.py`, so the two surfaces never drift apart.

```python
from ledger import chat_tools

tools = chat_tools()          # [MCPTool('add_transaction'), MCPTool('list_accounts')]
chat_tool = tools[0].into_chat_tool()   # chat.types.ChatTool for the API call
```

Feed the `ChatTool`s to a `chat` completion, then run the assistant's
tool calls with `chat.execute_tools(ctx, tools, messages)` — each
returns a `ChatMessageTool` with the directive text (or the error
payload) as content.

## Your first ledger

Create a file, say `first.bean`:

```beancount
option "title" "My First Ledger"
option "operating_currency" "USD"

1970-01-01 open Assets:Cash
1970-01-01 open Equity:Opening-Balances
1970-01-01 open Income:Salary
1970-01-01 open Expenses:Food

2024-01-01 * "Opening balance"
  Assets:Cash           1000.00 USD
  Equity:Opening-Balances

2024-01-15 * "Salary" "ACME Corp"
  Assets:Cash           2500.00 USD
  Income:Salary

2024-01-20 * "Groceries"
  Expenses:Food          120.45 USD
  Assets:Cash
```

Notes for a valid ledger:

- Every account must be declared with `open` before it is used.
- Every transaction must balance to zero. The last posting of
  "Groceries" has no amount — Beancount computes it from the other legs
  (this is called the *balancing* posting).
- Currency amounts need two places: `120.45` works, `120.4` does not.

Validate it:

```sh
uv run bean-check first.bean   # silence is good; errors print with file:line
```

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
| `connection()` | `() -> beanquery.Connection` | Cached beanquery connection. Reloads from disk when a stale mtime/size fingerprint is seen — the root file *and* everything it `include`s. Thread-safe (per-instance lock). |
| `connection_errors()` | `() -> List[LedgerError]` | Structured loader/validation errors carried by the cached connection. Empty list means the ledger is clean. |
| `check()` | `() -> List[LedgerError]` | Full re-parse of the ledger with `beancount.loader` (ignores the cache). Slower than `connection_errors()`; use it when you want a fresh validation regardless of staleness. |
| `invalidate()` | `() -> None` | Force the next `connection()` call to reload from disk. |

### Staleness semantics

The library assumes ledger files are never edited by hand while a program
runs. There is **no watcher**: on each `connection()` access the known
ledger files (root + includes) are fingerprinted (`mtime_ns`, `size`);
any mismatch triggers a reload. Appends made by this library's own write
bindings flush before returning, so the next access observes them.

Includes are resolved relative to the ledger file's directory (this
covers `include "ledgers/2026.beancount"` in a nested layout).

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

`LedgerError = Dict[str, object]`; `FileState = Dict[Path, Optional[Tuple[int, int]]]`
are exported type aliases.

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

errors = mgr.connection_errors()   # structured loader/validation errors
assert errors == []
```

BQL is SQL-like, but with two caveats:

- `FROM` is a date/filter clause, not a table selector.
- Multi-currency sums return an `Inventory`, not a number — wrap with
  `convert()` to force one currency:

```sql
SELECT convert(sum(position), 'USD') WHERE account ~ 'Assets|Liabilities'
-- => 2529.55 USD
```

Useful queries (verified against `examples/main.bean`):

```sql
-- spending by expense account
SELECT account, sum(position) WHERE account ~ 'Expenses' GROUP BY account ORDER BY account

-- recent postings to an account (always add LIMIT at row level)
SELECT date, payee, narration, account, position
WHERE account ~ 'Assets:Bank' ORDER BY date DESC LIMIT 50
```

### `run_query` — paged BQL results

```python
from ledger import run_query

out = run_query(mgr, "SELECT account, sum(position) GROUP BY account ORDER BY account")
# out.columns        -> ['account', 'sum(position)']
# out.rows           -> [['Assets:Bank:Checking', '(-320.45 USD)'], ...]  (strings)
# out.truncated      -> False
# out.total_rows     -> 7
```

Row values are strings; `sum(position)` renders as an `Inventory` (the
parens are Beancount's string form). Use `convert()` in BQL for plain
numbers.

Results are capped at 200 rows (`ROW_LIMIT`). Pass `offset` to page past
the cap:

```python
page2 = run_query(mgr, "SELECT date, narration WHERE account ~ 'Assets'", offset=200)
assert page2.offset == 200
```

A syntactically invalid query returns the model with `error_type="bql"`
and the beanquery error message in `error` — it does not raise.

### High-level helpers

All helpers take the `LedgerManager` and return a pydantic model (see
[Result models](#result-models)). On failure the model's `error` /
`error_type` fields are set instead of the normal fields.

#### Orientation

```python
from ledger import ledger_info, date_range

info = ledger_info(mgr)
# info.operating_currency -> "USD"   (first configured operating_currency)
# info.title              -> "Sample Ledger"
# info.today              -> "2026-08-19"   (today's date, ISO)
# info.date_range         -> DateRange(first="2024-01-01", last="2024-03-20")
# info.account_count      -> 7
# info.account_roots      -> ["Assets", "Equity", "Expenses", "Income"]

span = date_range(mgr)
# span.first -> "2024-01-01", span.last -> "2024-03-20"
```

`date_range` covers dates that have at least one posting — bare
open/close/price directives are invisible to it.

#### Validation

```python
from ledger import bean_check

out = bean_check(mgr)
# out.ok -> True; out.message -> "Ledger is clean — no errors or warnings."
# on a broken ledger: out.ok -> False, out.errors -> [LedgerIssue(...), ...]
```

`bean_check` reuses the cached connection's errors (like
`connection_errors()`), `LedgerManager.check()` does a full re-parse.

#### Accounts and commodities

```python
from ledger import (
    account_names, list_accounts,
    commodity_names, list_commodities,
)

account_names(mgr)    # -> ["Assets:Bank:Checking", "Assets:Broker", ...]  sorted
list_accounts(mgr)    # -> AccountsList(accounts=[...], count=7)

commodity_names(mgr)  # -> ["AAPL", "USD"]   sorted
list_commodities(mgr) # -> CommoditiesList(commodities=["AAPL", "USD"], count=2)
```

`commodity_names` is the union of three sources: declared `commodity`
directives, the `prices` table, and every currency that appears in a
posting. There is no 200-row cap on the `list_*` helpers.

#### Tables and prices

```python
from ledger import table_names, list_tables, list_prices

table_names(mgr)   # -> ['accounts', 'balances', 'commodities', 'documents',
                   #     'entries', 'events', 'notes', 'postings', 'prices',
                   #     'transactions']   (sorted)
list_tables(mgr)   # -> TablesList(tables=[...], warning="In BQL, FROM is a ...")

prices = list_prices(mgr)
# prices.prices -> [Price(commodity="AAPL", date="2024-02-15", price="185.00 USD")]
```

`list_prices` returns the latest price per commodity from the `prices`
table; for multiple prices on the same day, the last one in the file
wins (Beancount's rule).

---

## Writing directives

### The `make_/format_/append_/add_` convention

Every write binding follows the same four-function shape:

- **`make_<type>(...)`** — build the `beancount.core.data` object from
  plain Python values. Amounts are coerced with `Decimal(str(...))`, so
  strings (`"8.50"`), `int`, and `Decimal` all work.
- **`format_<type>(entry)`** — render it exactly like a hand-written
  ledger file (including the blank line between directives).
- **`append_<type>s(path, entries)`** — append several rendered
  directives. The file is opened in append mode and **flushed**, so the
  change is visible to the next `LedgerManager` staleness check.
- **`add_<type>(path, ...)`** — make one, append it, and validate. The
  directive is **routed to the canonical layout file** (see [The repo's finance ledger](#the-repos-finance-ledger)): `open` /
  `close` go to the `accounts` file, `commodity` / `price` to the
  `commodities` file, and transactions + the other dated directives
  (balance, pad, note, document, event, query, custom) to
  `ledgers/<YEAR>.bean` — falling back to `path` itself for single-file
  ledgers. Returns `(entry, errors)` — a tuple of the built directive and
  the list of `LedgerError`s. **Validation happens *before* any write:
  an empty `errors` list means it was written and the ledger is still
  clean; on error nothing is modified.** A transaction routed to a year
  file the root does not include yet also gets an `include` line appended
  to the root (once) so the entry is actually part of the ledger.
  The raw `append_<type>s` functions never validate and write exactly
  where you tell them.

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
tags=None, links=None, filename="<transaction>") -> data.Transaction`.
`postings` accepts ready-made `data.Posting` objects or tuples:

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

Append-and-revalidate in one step:

```python
txn, errors = add_transaction(Path("first.bean"), txn.date, txn.narration, [
    ("Expenses:Food", "8.50", "USD"),
    ("Assets:Cash", None),
])
assert errors == []
```

Also exported: `make_posting(spec) -> data.Posting`, `append_transactions(path, txns)`,
and the `PostingSpec` / `CostSpec` type aliases.

### Accounts (`ledger/accounts.py`)

```python
from ledger import add_account, close_account

open_, errors = add_account(
    Path("first.bean"), date(2024, 1, 1), "Assets:Broker",
    currencies=["USD", "AAPL"], booking="FIFO",
)
assert errors == []          # loader errors if the append broke the ledger

close, errors = close_account(Path("first.bean"), date(2024, 12, 31), "Assets:Broker")
```

- `add_account(path, date, account, *, currencies=None, booking=None)
  -> (Open, errors)`. `booking` is `None`, a `data.Booking` enum, or a
  method name like `"FIFO"` / `"STRICT"` (case-insensitive); `currencies`
  restricts the account to those commodities (omit for unrestricted).
- `close_account(path, date, account) -> (Close, errors)`.

Builders/formatters: `make_open(date, account, *, currencies=None,
booking=None, filename="<open>")`, `make_close(date, account)`,
`format_account(entry)`, `append_accounts(path, entries)`; `AccountEntry
= Union[Open, Close]`.

### Commodities (`ledger/commodities.py`)

```python
from ledger import add_commodity

commodity, errors = add_commodity(Path("first.bean"), date(2024, 9, 1), "BTC")
assert errors == []
```

- `add_commodity(path, date, currency) -> (Commodity, errors)`.
- Builders/formatters: `make_commodity(date, currency, *,
  filename="<commodity>")`, `format_commodity`, `append_commodities`.

### The remaining directive types (`ledger/directives.py`)

Covers every other directive type in the Beancount manual. Each type
exposes the same four functions:

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

The `format_<type>` / `append_<type>s` counterparts are `format_balance`,
`format_pad`, ..., `append_balances`, `append_pads`, ... .

```python
from decimal import Decimal
from ledger import add_balance, add_price, add_event, add_custom

# Assert an account's balance (optionally with a tolerance ~ N)
add_balance(Path("first.bean"), date(2024, 6, 1), "Assets:Cash", "154.20", "USD")
add_balance(Path("first.bean"), date(2024, 6, 1), "Assets:Cash",
            "319.020", "USD", tolerance=Decimal("0.002"))

# Declare a price point (one HOOL is worth 579.18 USD)
add_price(Path("first.bean"), date(2024, 7, 9), "HOOL", "579.18", "USD")

# Track a dated variable (location, employer, trading window...)
add_event(Path("first.bean"), date(2024, 7, 9), "location", "Paris, France")

# Prototype directive types with custom values
add_custom(Path("first.bean"), date(2024, 7, 9), "budget",
           ["groceries", Decimal("45.30")])
```

`custom` values are coerced to Beancount's `ValueType(value, dtype)`:
`bool` → `bool`, `int`/`float`/`Decimal` → `Decimal`, `date` → `date`,
`Amount` → `amount`, `str` → `str`; ready-made `ValueType`s pass through.

Validation behavior that follows Beancount's own rules (all surfaced via
the returned `errors` list):

- `add_pad` alone is rejected ("unused pad") unless a later balance
  assertion on the padded account needs it to fill a difference — append
  the pad and its assertion together.
- `add_balance` asserts against the ledger's real balance at that date;
  the assertion fails if it doesn't match.
- `add_document` requires the referenced file to exist on disk.

---

## Result models (`ledger.models`)

Every query helper returns one of these pydantic models. All of them
inherit `Base`, which carries the error fields. Serialize with
`.model_dump()` (plain dict) or `.model_dump_json()`.

| Model | Fields | Returned by |
|---|---|---|
| `Base` | `error: Optional[str]`, `error_type: Optional[str]`, `errors: List[LedgerIssue]` | — (base class) |
| `LedgerIssue` | `file: Optional[str]`, `line: Optional[int]`, `type: Optional[str]`, `message: Optional[str]` | `bean_check`, error paths |
| `DateRange` | `first: Optional[str]`, `last: Optional[str]` (ISO dates) | `date_range`, `ledger_info.date_range` |
| `LedgerInfo` | `today: str`, `title: Optional[str]`, `operating_currency: Optional[str]`, `date_range: DateRange`, `account_count: int`, `account_roots: List[str]` | `ledger_info` |
| `QueryResult` | `columns: List[str]`, `rows: List[List[str]]`, `truncated: bool`, `returned_rows: int`, `offset: int`, `total_rows: Optional[int]`, `total_rows_known: bool` | `run_query` |
| `CheckResult` | `ok: bool`, `message: str` | `bean_check` |
| `AccountsList` | `accounts: List[str]`, `count: int` | `list_accounts` |
| `CommoditiesList` | `commodities: List[str]`, `count: int` | `list_commodities` |
| `TablesList` | `tables: List[str]`, `warning: str` | `list_tables` |
| `Price` | `commodity: str`, `date: str`, `price: str` | `list_prices` |
| `PricesList` | `prices: List[Price]`, `count: int` | `list_prices` |

### Error handling

- On success, `error` and `error_type` are `None` and the normal fields
  are populated.
- `error_type="bql"` — invalid BQL or invalid parameter (e.g. a quote in
  an account name). `error` holds the message; the call does **not**
  raise.
- `error_type="ledger"` — the ledger itself has loader/validation
  errors; `errors` repeats the individual `LedgerIssue`s.
- `QueryResult.total_rows` is exact only when `truncated` is `False`
  (i.e. `total_rows_known`); past the 200-row cap it is `None`.

---

## The repo's finance ledger

The canonical ledger is `finance/main.beancount` — EUR, with yearly
entries in `finance/ledgers/` and `open`/`commodity`/`price` directives
split into `finance/accounts.beancount` and `finance/commodities.beancount`
(some of which are still empty). Point a `LedgerManager` at it and use
the same API:

```python
from ledger import LedgerManager, ledger_info

mgr = LedgerManager("finance/main.beancount")
print(ledger_info(mgr).model_dump())
```

Ledger files use any extension — `.bean` and `.beancount` work
identically.

## Example ledger and playground

[`examples/`](examples/main.bean) is a small multi-currency ledger laid
out like a real one:

```
examples/
  main.bean          # options + includes + transactions + a price
  accounts.bean      # open directives
  commodities.bean   # commodity declarations
  example.py         # runnable playground script
```

The root [`examples/main.bean`](examples/main.bean) declares the
`operating_currency` and includes `accounts.bean` and `commodities.bean`,
exactly the split used by the repo's `finance/` ledger.

### Play with the example

`examples/example.py` demos the whole API and chews on a throwaway copy,
so it **never mutates the shipped example files** — run it as many times
as you like:

```sh
uv run python examples/example.py            # the examples/main.bean ledger
uv run python examples/example.py some.bean  # or point it at your own ledger
```

It prints, in order:

1. **Orientation** — `ledger_info` / `date_range`: title, operating
   currency, date span, account count and roots.
2. **Accounts** — `list_accounts`.
3. **Commodities** — `list_commodities` and `commodity_names`.
4. **Prices** — `list_prices` (latest price per commodity).
5. **Tables** — `table_names` and the `list_tables` caveat warning.
6. **A BQL query** — `run_query`, with per-account converted balances.
7. **Every write binding on a temp copy** — `add_transaction`,
   `add_account`, `add_commodity`, `add_balance`, `add_note`, `add_price`,
   `add_event`, `add_query`, and `add_custom`, each showing the appended
   directive and the loader-error count (0 = still a valid ledger),
   then the fully rendered result.

Trim or extend the `demo_read` / `demo_write` functions to explore; the
read demos run against the real ledger, the write demos always against a
temporary copy.

Run the test suite against the example ledger:

```sh
uv run pytest
```
