#!/bin/sh
# reader.sh — read-only puller container entrypoint.
#
# Every $PULL_INTERVAL seconds: fetch origin and hard-reset the working
# tree to origin/$BRANCH (readers never commit; history rewriting by the
# writer is handled because we reset, not merge).
#
# Env (defaults shown):
#   REPO_URL        required   ssh://git@host/path/repo.git
#   BRANCH          main
#   REPO_DIR        /repo
#   SSH_KEY_PATH    /root/.ssh/id_rsa
#   SSH_KNOWN_HOSTS /root/.ssh/known_hosts
#   PULL_INTERVAL   300

set -eu

: "${REPO_URL:?REPO_URL is required (e.g. ssh://git@host/valerii/finances.git)}"
: "${BRANCH:=main}"
: "${REPO_DIR:=/repo}"
: "${SSH_KEY_PATH:=/root/.ssh/id_rsa}"
: "${SSH_KNOWN_HOSTS:=/root/.ssh/known_hosts}"
: "${PULL_INTERVAL:=300}"

export PATH=/usr/bin:/bin:/usr/local/bin
export GIT_SSH_COMMAND="ssh -i $SSH_KEY_PATH -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$SSH_KNOWN_HOSTS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

ensure_repo() {
  git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true
  if [ ! -d "$REPO_DIR/.git" ]; then
    if [ -n "$(ls -A "$REPO_DIR" 2>/dev/null)" ]; then
      log "REPO_DIR $REPO_DIR is not empty and not a git repo; refusing to clone over it" >&2
      exit 1
    fi
    log "cloning $REPO_URL into $REPO_DIR"
    git clone "$REPO_URL" "$REPO_DIR"
  fi
}

trap 'log "shutting down"; exit 0' TERM INT

ensure_repo
cd "$REPO_DIR"

# One ledgerd process (reader or writer) per REPO_DIR, enforced by flock on
# the same lock file the writer uses. Intended deployment gives every
# container its own volume -- readers and writer share REPO_URL, never a
# REPO_DIR -- so contention means misconfiguration or debugging; the second
# starter exits here rather than racing a writer's commits / a reader's reset.
exec 9>"$REPO_DIR/.ledgerd.lock"
if ! flock -n 9; then
  log "another ledgerd instance already holds the lock on $REPO_DIR; exiting"
  exit 1
fi

while :; do
  if fetch_log="$(git fetch origin "$BRANCH" 2>&1)"; then
    local_ref="$(git rev-parse HEAD 2>/dev/null || true)"
    remote_ref="$(git rev-parse "origin/$BRANCH" 2>/dev/null || true)"
    if [ -n "$remote_ref" ] && [ "$local_ref" != "$remote_ref" ]; then
      # reset --hard destroys tracked modifications; keep a stash so an
      # operator who exec'd in and edited a file can recover them
      # (`git stash list` / `git stash pop`). Entries are never auto-dropped.
      if ! git diff --quiet || ! git diff --cached --quiet; then
        # best-effort only: under `set -e` a failed stash would kill the
        # whole loop (crash-loop); degrade to warn-and-reset instead
        if git stash push -m "ledgerd-reader $(date '+%Y-%m-%d %H:%M:%S')"; then
          log "preserving uncommitted local changes (git stash push)"
        else
          log "warning: git stash failed; discarding uncommitted changes"
        fi
      fi
      git reset --hard "origin/$BRANCH"
      log "updated to $(git log -1 --pretty='%h %s')"
    else
      log "up to date"
    fi
  else
    log "fetch failed: $fetch_log; retrying in ${PULL_INTERVAL}s"
  fi
  sleep "$PULL_INTERVAL"
done