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
    if [ -z "$(ls -A "$REPO_DIR")" ]; then
      log "cloning $REPO_URL into $REPO_DIR"
      git clone "$REPO_URL" "$REPO_DIR"
    else
      log "initializing $REPO_DIR from $REPO_URL"
      (cd "$REPO_DIR" && git init -b "$BRANCH" &&
       git remote add origin "$REPO_URL" &&
       git fetch origin && git reset --hard "origin/$BRANCH")
    fi
  fi
}

trap 'log "shutting down"; exit 0' TERM INT

ensure_repo
cd "$REPO_DIR"

while :; do
  if git fetch origin "$BRANCH" 2>/dev/null; then
    local_ref="$(git rev-parse HEAD 2>/dev/null || true)"
    remote_ref="$(git rev-parse "origin/$BRANCH" 2>/dev/null || true)"
    if [ -n "$remote_ref" ] && [ "$local_ref" != "$remote_ref" ]; then
      if ! git diff --quiet || ! git diff --cached --quiet ||
         [ -n "$(git ls-files --others --exclude-standard)" ]; then
        log "warning: discarding uncommitted local changes in $REPO_DIR"
      fi
      git reset --hard "origin/$BRANCH"
      log "updated to $(git log -1 --pretty='%h %s')"
    else
      log "up to date"
    fi
  else
    log "fetch failed; retrying in ${PULL_INTERVAL}s"
  fi
  sleep "$PULL_INTERVAL"
done