# ledger dev notes

Monorepo with two projects side by side, each with its own full git history
(the merge commit `f4bfaf6`; `HEAD^2` = ledgerd's original `master`):

- `ledger/` — beancount v3 query/edit utilities, MCP server, Telegram bot
- `ledgerd/` — single-writer / multi-reader git sync of ledger files
- `.github/` — CI at the repo root (GitHub only scans root workflows)

## Development

### ledger

```sh
cd ledger
uv sync --frozen --dev
uv run pytest -q      # 127 tests
uv run pyright        # typecheck
```

Private deps (`chat`, `mcp`) are fetched over SSH from
`ssh.git.valerii.casa`. CI installs a deploy key via
`.github/actions/setup-ssh-private` (`secrets.SSH_PRIVATE_KEY`); locally your
own SSH keys are used. Local `act` runs: see
`ledger/.agents/skills/act/SKILL.md`.

### ledgerd

Zero dependencies (`dependencies = []`). Syntax check:

```sh
cd ledgerd
python3 -m py_compile writer.py
bash -n reader.sh
```

Real runs need a `REPO_DIR`, an SSH key and a git remote — use the
Dockerfiles (`Dockerfile.writer`, `Dockerfile.reader`) instead.

## Versioning and releases

The two projects are versioned independently. A git tag sits on a single
commit and cannot carry two versions, so tags are prefixed per project; each
publish workflow triggers only on its own prefix:

| Git tag | Workflow | Images pushed to `git.valerii.casa` |
|---|---|---|
| `ledger-vX.Y.Z` | `ledger-docker-publish` | `valerii/ledger:vX`, `:vX.Y.Z`, `:latest` |
| `ledgerd-vX.Y.Z` | `ledgerd-docker-publish` | `valerii/ledgerd-writer` and `valerii/ledgerd-reader` — each `vX`, `vX.Y.Z`, `latest` |

Rules:

- Tag version must equal `version` in that project's `pyproject.toml`
  (extracted with `sed` from the first `version = "X.Y.Z"` line — no Python
  stdlib dependency, runners may lack `tomllib`; mismatch fails the workflow
  with `::error::`).
- Tag suffix must be strict `X.Y.Z` — no prerelease or partial suffixes.
- A bare `vX.Y.Z` tag (no prefix) triggers neither workflow.

Release flow:

```sh
# 1. bump `version` in ledger/pyproject.toml or ledgerd/pyproject.toml;
#    commit and push the bump first
git tag ledger-v0.1.0
git tag ledgerd-v0.1.0
git push origin ledger-v0.1.0 ledgerd-v0.1.0
```

Build notes:

- `latest` is refreshed on every tag push; there are no branch-triggered
  builds.
- ledger images are multi-arch (`linux/amd64`, `linux/arm64`) and install
  private deps through BuildKit SSH forwarding: the workflow loads
  `secrets.SSH_PRIVATE_KEY` into an agent and passes `ssh: default`
  (`Dockerfile.tg` uses `RUN --mount=type=ssh`).
- ledgerd builds both images in one matrix job: `Dockerfile.writer` →
  `ledgerd-writer`, `Dockerfile.reader` → `ledgerd-reader`. No SSH needed
  (zero deps).
- Registry login: `${{ github.actor }}` + `secrets.GITHUBTOKEN`; build cache
  reads/writes the `:latest` image (inline).
- Local builds: `docker compose -f ledger/docker-compose.tg.yaml up`
  (context `./ledger`, SSH from your local `~/.ssh`);
  `docker build -f ledgerd/Dockerfile.writer -t ledgerd-writer ledgerd/`.
