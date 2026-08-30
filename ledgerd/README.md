# ledgerd

Single-writer git sync of ledger files (`.bean`, `.jsonl`).

- **Writer** (`writer.py`, `Dockerfile`): the only container allowed to push.
  Every hour at `MM=59` it commits changes to `FILE_PATTERNS` as
  `<dd-mm-yyyy>-<hh>`. Every day at `23:59` it squashes each day's pending
  commits — including leftovers from previous days, e.g. `01-01-2026-01` +
  `01-01-2026-02` → `01-01-2026` — into a single `<dd-mm-yyyy>` commit and
  force-pushes with lease.

The writer takes a `flock` on `<REPO_DIR>/.ledgerd.lock`; a second instance
on the same `REPO_DIR` exits rather than racing. Run exactly one writer per
repo, each with its own volume.

## Requirements

- Private repo reachable over SSH (e.g. Gitea/GitHub).
- SSH private key — default `~/.ssh/id_rsa` — allowed to push. Mounted
  read-only into the container; the entrypoint copies it to a writable
  in-container path with mode `0600` at startup, so loose host-file
  permissions (e.g. `644`) never trigger OpenSSH's "UNPROTECTED PRIVATE KEY
  FILE" refusal.
- `TZ` matching your local time so the 23:59 day boundary aligns.

## Writer

```sh
docker build -t ledgerd .
docker run -d --name ledgerd --restart unless-stopped \
  -e REPO_URL=ssh://git@ssh.git.valerii.casa/valerii/finances.git \
  -e TZ=Europe/Kyiv \
  -v ~/.ssh/id_rsa:/root/.ssh/id_rsa:ro \
  -v "$PWD":/repo \
  ledgerd
```

## Environment variables

| Variable | Default | Writer |
|---|---|---|
| `REPO_URL` | — (required) | ✓ |
| `BRANCH` | `main` | ✓ |
| `REPO_DIR` | `/repo` | ✓ |
| `SSH_KEY_PATH` | `/root/.ssh/id_rsa` | ✓ |
| `SSH_KNOWN_HOSTS` | `/root/.ssh/known_hosts` | ✓ |
| `FILE_PATTERNS` | `*.bean *.jsonl` | ✓ |
| `GIT_NAME` / `GIT_EMAIL` | `Finances Writer` / `writer@finances.local` | ✓ |
| `PUSH_RETRIES` | `5` | ✓ |
| `PUSH_RETRY_SLEEP` | `30` | ✓ |
| `TICK_SLEEP` | `30` | ✓ |

First SSH connection accepts the host key (`StrictHostKeyChecking=accept-new`)
into `$SSH_KNOWN_HOSTS`, so an empty mounted file works for bootstrapping.

The image ships a `HEALTHCHECK`: each successful writer tick touches
`<REPO_DIR>/.ledgerd.health`, and the probe fails when that stamp is older
than `2 * TICK_SLEEP` plus 90s, so `docker ps`/monitoring shows `unhealthy`
when the container stops making progress (e.g. stuck on a bad key or
network).

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