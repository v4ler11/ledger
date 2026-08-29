---
name: act
description: >-
  Run and validate this project's Forgejo Actions workflows locally with `act`
  (Docker-based GitHub/Forgejo Actions runner). Required wrapper for any agent
  exercising the pyright or test CI jobs on a dev machine, including handling
  the node:20 job image, Apple Silicon container architecture, and the SSH key
  for private git dependencies.
globs:
  - .github/workflows/*.yml
  - .github/actions/**/action.yml
---

# act — run Forgejo workflows locally

The CI lives in `.github/workflows/` (Forgejo falls back to `.github/` when
`.forgejo/` is absent). `act` executes those same workflows in Docker, so you can
validate them without a running Forgejo runner.

## Context: what these workflows need

| Workflow | Job | Runs |
|----------|-----|------|
| `pyright.yml` | `pyright` | `uv run pyright` (0 errors/0 warnings is green) |
| `test.yml`    | `test`    | `uv run pytest -q` (127 passed is green) |

Both jobs reuse the composite action `.github/actions/setup-ssh-private/` to
install an SSH deploy key so `uv sync --frozen --dev` can clone the **private**
dependencies `chat` and `mcp` from `ssh://git@ssh.git.valerii.casa/valerii/chat.git`
(via `[tool.uv.sources]` in `pyproject.toml`).

Without a key the SSH step **no-ops** (prints a warning) — then `uv sync` inside
the container fails because it cannot reach the private git host. So a real run
needs the key; a dry-run does not.

## Invocation

Binary: `act` at `/opt/homebrew/bin/act` (installed via Homebrew). Requires a
running Docker daemon (Docker Desktop / the engine reachable at
`unix:///var/run/docker.sock`).

### 1. Validate the workflows parse (fast, no side effects)

```bash
act -n                          # dry-run ALL workflows
act -n -W .github/workflows/pyright.yml   # one workflow
```

A parse/ref error fails earlier than this project's workflows ever did — the
current ones plan cleanly end to end.

### 2. Real run — always override the job image

`act` maps `ubuntu-latest` → `node:16-buster-slim` by default, which is too old
for pyright (needs Node ≥ 18). Override to node:20:

```bash
act -P ubuntu-latest=node:20-bullseye
```

On an Apple M-series host, `act` prints a warning about container architecture;
use the full form if arm64 images misbehave:

```bash
act -P ubuntu-latest=node:20-bullseye --container-architecture linux/amd64
```

### 3. Provide the SSH deploy key for private deps

Pass a key that can read `valerii/chat.git` (a `~/.ssh` key or a repo deploy
key):

```bash
act -s SSH_PRIVATE_KEY="$(cat ~/.ssh/<your-key>)"
```

Leaving it unset makes `uv sync` fail inside the container (see Context).
Only reachable for the `steps.ssh` that consumes `inputs.ssh_private_key`; the
steps are listed in the SKILL, but let the workflow file be the source of truth
for the exact secret name.

### Combined full run (single workflow, dry-run is `-n`):

```bash
act -P ubuntu-latest=node:20-bullseye \
    -s SSH_PRIVATE_KEY="$(cat ~/.ssh/<your-key>)"
```

## Project conventions to preserve

- Workflows target `runs-on: ubuntu-latest`; step image is set via `-P` at the
  CLI, **never** hardcoded in the YAML job.
- `uv sync --frozen --dev` is required so CI rejects a stale `uv.lock` instead
  of silently re-resolving. Keep `uv.lock` in sync (`uv lock --check`) before
  pushing workflow changes.
- Reusable logic goes in `.github/actions/<name>/action.yml` composite actions
  and is referenced with a local path `uses: ./.github/actions/<name>` —
  `act` (and Forgejo) reject full-URL action refs.
- The `SSH_PRIVATE_KEY` secret is referenced as
  `${{ secrets.SSH_PRIVATE_KEY }}` in workflows; the composite action reads it
  through an input (`ssh_private_key`) and passes a multi-line PEM via `env`,
  never by splicing it into a `run:` script.

## Verification

After a green run, confirm locally what the workflow will assert:

```bash
uv run pyright          # expect: 0 errors, 0 warnings
uv run pytest -q        # expect: 127 passed
```

If `act` reports a job failure, the cause is almost always environment, not the
workflow: node:16 default image (fix with `-P …=node:20-bullseye`), a missing
SSH key (fix with `-s SSH_PRIVATE_KEY=…`), or no Docker daemon reachable.
