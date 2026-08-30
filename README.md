# ledger — AI-native personal accounting on Beancount + git

Your finances live in a plain-text double-entry ledger (Beancount), versioned
and synced through git, and you talk to them in natural language: an
LLM-backed agent writes BQL queries, posts transactions, and answers
questions — over MCP (editors, Claude, any MCP client) or a Telegram bot.
Every write is validated against the whole ledger before it touches disk,
and a background worker (`ledgerd`) commits and pushes changes on a schedule.

```
┌──────────────────────────────┐     ┌───────────────────────────────┐
│  MCP client (Claude, editor) │     │  Telegram bot (any device)    │
└──────────────┬───────────────┘     └──────────────┬────────────────┘
               │ MCP (stdio / HTTP-SSE)             │ aiogram + chat lib
               ▼                                    ▼
        ┌──────────────────────────────────────────────────────┐
        │  ledger/ — Python library + MCP server + TG bot      │
        │  LedgerManager (beanquery, mtime-cached, validated)  │
        │  write bindings (append + re-validate before write)  │
        │  run_query (BQL) · receipts · recurring              │
        └──────────────────────────────┬───────────────────────┘
                                       │ append / query 
                                       ▼
        ┌──────────────────────────────────────────────────────┐
        │  Beancount ledger tree (main.bean + ledgers/<YEAR>)  │
        │  receipts/<YEAR>.jsonl                               │
        └──────────────────────────────┬───────────────────────┘
                                       │ hourly commits + daily squash
                                       ▼
        ┌──────────────────────────────────────────────────────┐
        │  ledgerd/ — single-writer git sync, push over SSH    │
        └──────────────────────────────────────────────────────┘
```

## What it is

A personal-accounting stack built on two boring, durable foundations:

- **Beancount v3** — the source of truth is a plain-text, double-entry
  ledger: diffable, greppable, fixable by hand, and queryable with BQL
  (Beancount Query Language, SQL-like). No database, no lock-in.
- **git** — the ledger tree is a git repo. `ledgerd` commits changes
  hourly (`<dd-mm-yyyy>-<hh>`) and squashes each day into one commit at
  23:59, then force-pushes with lease. Every transaction is versioned and
  backed up; nothing is ever silently lost.

On top of that sits an agent layer that makes the ledger *usable*: instead
of hand-writing BQL or editing files, you ask for what you want and a model
(gemini via OpenRouter) composes the query or the transaction, invokes it
through the same validated tools, and reports what it did.

## The two projects

### `ledger/` — library, MCP server, Telegram bot

Pure-Python library for querying and editing Beancount v3 ledgers, plus
two agent interfaces built on it:

- **`LedgerManager`** — a cached beanquery connection with mtime-based
  staleness checks and structured `bean-check` validation. Reads never see
  a stale ledger; writes never leave a broken one.
- **Write bindings** — build and append every Beancount directive type
  (transactions, accounts, commodities, balance/pad/note/document/price/
  event/query/custom). Routing follows the canonical layout; validation
  runs *before* any write — on error, nothing is modified.
- **Query helpers** — paged `run_query`, accounts, commodities, prices,
  orientation, all returning pydantic models.
- **MCP server** — the same tools over MCP with two transports: stdio
  (spawn `uv run mcp_stdio`) and HTTP/SSE (`uv run mcp_server`, endpoint
  `http://127.0.0.1:8000/mcp`). Register it with Claude Desktop, an
  editor, or the `mcp` CLI.
- **Telegram bot** — chat with your finances from any device. Send text
  or receipt photos; the bot runs the same tools and summarizes what it
  did (tool calls, results, errors).

The agent-facing tool registry (11 tools):

| Tool | What the agent can do |
|---|---|
| `run_query` | Run any BQL statement against the ledger, read-only, paged — the model's BQL grammar and examples live in the tool description |
| `add_transaction` | Append a validated transaction (auto-routed to the right year file, auto-tagged) |
| `list_accounts` / `list_balances` | Confirm exact account names / balances before posting |
| `add_account` | Open an account (with currencies / booking) |
| `add_receipt` / `get_receipts_by_ids` | Archive purchase evidence as JSON records in `receipts/<YEAR>.jsonl`; fetch them back |
| `add_recurring` / `list_recurring` / `update_recurring` / `delete_recurring` | Manage recurring transactions |

### `ledgerd/` — the git side

A zero-dependency single-writer container: every hour it commits
`*.bean` / `*.jsonl` changes, at 23:59 it squashes each day's commits into
one and force-pushes (with lease) over SSH. Exactly one writer per repo;
`flock`-guarded against accidental second instances, with a healthcheck so
a stuck container shows `unhealthy`.

## Why this design

- **The ledger never breaks.** Every append is staged against a copy of the
  whole tree and re-parsed with beancount before persisting. A bad
  transaction, a duplicate account, or a wrongly-dated posting is rejected
  with file/line errors — the real ledger is untouched.
- **Agents write the boring parts, you keep control.** The ledger stays
  hand-editable text; the AI is a front-end to a strict, validated API.
- **Automation with a paper trail.** git history is the audit log: every
  hourly commit and daily squash is attributable, revertible, and
  force-push-with-lease keeps the branch safe from other writers.

## Quick start

Requires Python ≥ 3.13 and [uv](https://docs.astral.sh/uv/).

```sh
cd ledger
uv sync --frozen --dev
uv run pytest        # tests (CI also runs pyright)
```

Point the tools at your ledger and run the MCP stdio server:

```sh
export LEDGER_PATH=/path/to/main.bean
uv run mcp_stdio     # register as a stdio MCP server in your client
```

Or run the Telegram bot and/or the HTTP MCP server:

```sh
export LEDGER_TG_BOT_TOKEN=... LEDGER_TG_TARGET_USER_ID=... \
       LEDGER_OPENROUTER_API_KEY=... LEDGER_PATH=/path/to/main.bean
uv run serve tg      # bot only
uv run serve mcp     # MCP HTTP/SSE only (http://127.0.0.1:8000/mcp)
uv run serve tg mcp  # both, side by side
```

There is a runnable playground that demos the whole API against a
throwaway copy of an example ledger (`examples/main.bean`):

```sh
uv run python examples/example.py
```

Prod deployment via the root `docker-compose.yml`: `ledgerd` writer +
the bot/MCP server sharing one repo checkout. See `ledger/README.md`,
`ledgerd/README.md`, and `README.dev.md` for the details, layout, and
release flow (tags `ledger-v*` / `ledgerd-v*` trigger the Docker publish
workflows).

## Upstream note — private `chat` dependency

This repo is mirrored to GitHub, but two of its dependencies still point at
the private git host. Before building from the GitHub mirror, substitute
the `chat` (and `mcp`) sources in `ledger/pyproject.toml`:

```toml
# ledger/pyproject.toml — [tool.uv.sources]
chat = { git = "git@github.com:v4ler11/chat.git" }
mcp  = { git = "git@github.com:v4ler11/chat.git", subdirectory = "src/mcp" }
```

The same `chat` source line appears in `ledger/src/tg_bot/pyproject.toml`
and must be updated the same way. CI installs its SSH key via
`.github/actions/setup-ssh-private` (`secrets.SSH_PRIVATE_KEY`); use a key
that can reach GitHub instead of `ssh.git.valerii.casa`.