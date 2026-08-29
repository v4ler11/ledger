# ledgerd

Single-writer / many-readers git sync of ledger files (`.bean`, `.jsonl`).

- **Writer** (`writer.py`, `Dockerfile.writer`): in the container the only
  container allowed to push. Every hour at `MM=59` it commits changes to
  `FILE_PATTERNS` as `<dd-mm-yyyy>-<hh>`. Every day at `23:59` it squashes
  each day's pending commits — including leftovers from previous days, e.g.
  `01-01-2026-01` + `01-01-2026-02` → `01-01-2026` — into a single
  `<dd-mm-yyyy>` commit and force-pushes with lease.
- **Reader** (`reader.sh`, `Dockerfile.reader`): read-only mirror. Fetches
  every `PULL_INTERVAL` seconds (default 5 min) and hard-resets the working
  tree to `origin/$BRANCH`, so the writer's daily rewrites are followed
  without conflict. Never pushes.

## Requirements

- Private repo reachable over SSH (e.g. Gitea/GitHub).
- SSH private key — default `~/.ssh/id_rsa` — allowed to push (writer) or
  read (reader). Mounted read-only into the container.
- `TZ` matching your local time so the 23:59 day boundary aligns.

## Writer

```sh
docker build -f Dockerfile.writer -t ledgerd-writer .
docker run -d --name ledgerd-writer --restart unless-stopped \
  -e REPO_URL=ssh://git@ssh.git.valerii.casa/valerii/finances.git \
  -e TZ=Europe/Kyiv \
  -v ~/.ssh/id_rsa:/root/.ssh/id_rsa:ro \
  -v "$PWD":/repo \
  ledgerd-writer
```

Run exactly one writer per repo.

## Reader

```sh
docker build -f Dockerfile.reader -t ledgerd-reader .
docker run -d --name ledgerd-reader --restart unless-stopped \
  -e REPO_URL=ssh://git@ssh.git.valerii.casa/valerii/finances.git \
  -e TZ=Europe/Kyiv \
  -v ~/.ssh/id_rsa:/root/.ssh/id_rsa:ro \
  -v "$PWD":/repo \
  ledgerd-reader
```

Run as many readers as you like.

## Environment variables

| Variable | Default | Writer | Reader |
|---|---|---|---|
| `REPO_URL` | — (required) | ✓ | ✓ |
| `BRANCH` | `main` | ✓ | ✓ |
| `REPO_DIR` | `/repo` | ✓ | ✓ |
| `SSH_KEY_PATH` | `/root/.ssh/id_rsa` | ✓ | ✓ |
| `SSH_KNOWN_HOSTS` | `/root/.ssh/known_hosts` | ✓ | ✓ |
| `FILE_PATTERNS` | `*.bean *.jsonl` | ✓ | — |
| `GIT_NAME` / `GIT_EMAIL` | `Finances Writer` / `writer@finances.local` | ✓ | — |
| `PUSH_RETRIES` | `5` | ✓ | — |
| `PUSH_RETRY_SLEEP` | `30` | ✓ | — |
| `TICK_SLEEP` | `30` | ✓ | — |
| `PULL_INTERVAL` | `300` | — | ✓ |

First SSH connection accepts the host key (`StrictHostKeyChecking=accept-new`)
into `$SSH_KNOWN_HOSTS`, so an empty mounted file works for bootstrapping.

## Limits

- Generic push hooks (e.g. GitHub Actions `publish` trigger) would fire on
  every hourly push *and* on the daily force-push; exclude the `ghooks.gitea.io` /
  GitHub webhook paths if you enable hooks on the repo.
- The writer force-pushes the daily squash (`--force-with-lease`), so the
  branch must not receive commits from other writers. Manual commits are
  preserved (writer commits carry an `X-Ledger-Writer: true` trailer; a
  manual commit whose message looks like `dd-mm-yyyy` is never absorbed).
- If a manual commit conflicts with a pending hourly commit, the rebase is
  aborted and reported as CRITICAL; the writer keeps running and skips
  merging until the divergence is resolved by hand (`git rebase --continue`
  after fixing the conflict, or pushing the local commit).