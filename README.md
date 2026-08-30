# ledger monorepo

Two projects side by side, each with its own full git history (merged via second parent of the monorepo merge commit):

- [`ledger/`](ledger/) — personal finance ledger in [beancount](https://beancount.org/) format: Python library, MCP server, Telegram bot, CI (`.github/`), Docker packaging (`Dockerfile`, `docker-compose.tg.yaml`, plus server/client
compose pairs `docker-compose.server.yml`, `docker-compose.client.yml` and
their `.dev` local-build variants). See [ledger/README.md](ledger/README.md).
  - Venv: `ledger/.venv`; run from `ledger/` (`uv sync --frozen --dev`, `uv run pytest`, `uv run pyright`).
  - compose: `docker compose -f ledger/docker-compose.tg.yaml up` (build context is `./ledger`).
- [`ledgerd/`](ledgerd/) — single-writer / multi-reader git sync of ledger files: `writer.py`, `reader.sh`, two Dockerfiles. See [ledgerd/README.md](ledgerd/README.md).

## Versioning

Tag scheme, publish workflows and release flow: see
[README.dev.md](README.dev.md) — Versioning and releases.

## History

- The `ledger` project was moved into `ledger/` (commit `ba83a24`, renames keep history).
- `ledgerd` was merged in at `ledgerd/` with its original history preserved: the merge commit `5c6f3d5` has `HEAD^2` = ledgerd's `master` (5 commits, tip `6adc23c`). `git log --first-parent` walks the monorepo line; `git log HEAD^2` walks ledgerd's line.