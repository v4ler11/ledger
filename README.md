# ledger monorepo

Two projects side by side, each with its own full git history (merged via second parent of the monorepo merge commit):

- [`ledger/`](ledger/) — personal finance ledger in [beancount](https://beancount.org/) format: Python library, MCP server, Telegram bot, CI (`.github/`), Docker packaging (`Dockerfile.tg`, `docker-compose.tg.yaml`). See [ledger/README.md](ledger/README.md).
  - Venv: `ledger/.venv`; run from `ledger/` (`uv sync --frozen --dev`, `uv run pytest`, `uv run pyright`).
  - compose: `docker compose -f ledger/docker-compose.tg.yaml up` (build context is `./ledger`).
- [`ledgerd/`](ledgerd/) — single-writer / multi-reader git sync of ledger files: `writer.py`, `reader.sh`, two Dockerfiles. See [ledgerd/README.md](ledgerd/README.md).

## Versioning

The two projects are versioned independently. A git tag applies to one commit
and cannot encode two versions, so tags carry the project prefix; each
workflow triggers only on its own prefix:

| Git tag | Images pushed to `app.git.valerii.cc` | Workflow |
|---|---|---|
| `ledger-vX.Y.Z` | `valerii/ledger:vX`, `valerii/ledger:vX.Y.Z`, `valerii/ledger:latest` | `ledger-docker-publish` |
| `ledgerd-vX.Y.Z` | `valerii/ledgerd-writer` + `valerii/ledgerd-reader` (same three tags each) | `ledgerd-docker-publish` |

The tag version must equal `version` in that project's `pyproject.toml` — the
workflow fails otherwise.

```sh
git tag ledger-v0.1.0
git tag ledgerd-v0.1.0
git push origin ledger-v0.1.0 ledgerd-v0.1.0
```

## History

- The `ledger` project was moved into `ledger/` (commit `ba83a24`, renames keep history).
- `ledgerd` was merged in at `ledgerd/` with its original history preserved: the merge commit `5c6f3d5` has `HEAD^2` = ledgerd's `master` (5 commits, tip `6adc23c`). `git log --first-parent` walks the monorepo line; `git log HEAD^2` walks ledgerd's line.