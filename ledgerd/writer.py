#!/usr/bin/env python3
"""ledgerd writer -- single-writer container entrypoint.

Once per day at 23:59, commit every changed file matching $FILE_PATTERNS as
a single "<dd-mm-yyyy>" commit and push to origin/$BRANCH. That's it: no
locks, no squash/merge, no retries. If a day's 23:59 window is missed, it's
skipped -- no catch-up.

Env contract (defaults shown):
  REPO_URL         required   ssh://git@host/path/repo.git
  BRANCH           main
  REPO_DIR         /repo
  SSH_KEY_PATH     /root/.ssh/id_rsa
  SSH_KNOWN_HOSTS  /root/.ssh/known_hosts
  FILE_PATTERNS    "*.bean *.jsonl"   (whitespace-separated; no spaces in names)
  GIT_NAME/GIT_EMAIL   commit identity
  TICK_SLEEP       30   loop granularity (seconds)
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import os
import shutil
import signal
import subprocess
import sys
import time
from typing import Optional


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], env=ENV, capture_output=True, text=True)


def run(*args: str) -> str:
    r = git(*args)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)!r} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def try_run(*args: str) -> Optional[str]:
    r = git(*args)
    return r.stdout if r.returncode == 0 else None


# --- repo bootstrap ---------------------------------------------------------

def ensure_repo() -> None:
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", REPO_DIR],
                   env=ENV, capture_output=True, text=True)
    if os.path.isdir(os.path.join(REPO_DIR, ".git")):
        return
    if not os.path.isdir(REPO_DIR) or not os.listdir(REPO_DIR):
        log(f"cloning {REPO_URL} into {REPO_DIR}")
        run("clone", REPO_URL, REPO_DIR)
    else:
        log(f"initializing {REPO_DIR} from {REPO_URL}")
        run("init", "-b", BRANCH)
        run("remote", "add", "origin", REPO_URL)
        run("fetch", "origin")
        if try_run("rev-parse", "--verify", f"origin/{BRANCH}"):
            run("reset", "--hard", f"origin/{BRANCH}")
        # else: remote is empty; local BRANCH stays unborn (commit creates it)
    ensure_branch()


def ensure_branch() -> None:
    """Current branch must be $BRANCH, not whatever clone's HEAD defaulted to."""
    cur = (try_run("branch", "--show-current") or "").strip()
    if cur == BRANCH:
        return
    if try_run("rev-parse", "--verify", f"origin/{BRANCH}"):
        run("checkout", "-b", BRANCH, f"origin/{BRANCH}")
        log(f"checked out {BRANCH} tracking origin/{BRANCH} (was '{cur}')")
    else:
        run("checkout", "-B", BRANCH)
        log(f"created local branch {BRANCH} (remote has no such branch; was '{cur}')")


# --- git operations ---------------------------------------------------------

def matching_files() -> list[str]:
    """Concrete files under REPO_DIR matching FILE_PATTERNS. `git add` with a
    raw glob fails on unmatched patterns, so pass real paths."""
    hits: list[str] = []
    for root, dirs, files in os.walk(REPO_DIR):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            if any(fnmatch.fnmatch(f, p) for p in PATTERNS):
                hits.append(os.path.join(root, f))
    return hits


def has_changes() -> bool:
    r = git("status", "--porcelain", "--", *PATTERNS)
    return bool(r.stdout.strip())


def commit_and_push() -> None:
    if not has_changes():
        log(f"no changes in [{FILE_PATTERNS}]")
        return
    files = matching_files()
    run("add", "--", *files)
    name = dt.date.today().strftime("%d-%m-%Y")
    run("commit", "-m", name)
    log(f"committed '{name}'")
    r = git("push", "origin", BRANCH)
    if r.returncode != 0:
        log(f"push failed: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'unknown'}")


# --- main loop --------------------------------------------------------------

def main() -> None:
    def _bye(*_: object) -> None:
        sys.exit(0)
    signal.signal(signal.SIGTERM, _bye)

    ensure_repo()
    os.chdir(REPO_DIR)

    # no catch-up: a missed 23:59 window is simply skipped
    last_daily = dt.date.today().strftime("%Y-%m-%d")

    while True:
        now = dt.datetime.now()
        slot_daily = now.strftime("%Y-%m-%d")
        try:
            if now.hour == 23 and now.minute == 59 and last_daily != slot_daily:
                commit_and_push()
                last_daily = slot_daily
        except Exception as e:
            log(f"scheduled step failed: {e}")
        # heartbeat for the image HEALTHCHECK (staleness = stuck loop)
        try:
            with open(os.path.join(REPO_DIR, ".ledgerd.health"), "w"):
                pass
        except OSError:
            pass
        time.sleep(TICK_SLEEP)


# --- config / env -----------------------------------------------------------

REPO_URL = os.environ.get("REPO_URL", "")
if not REPO_URL:
    sys.exit("REPO_URL is required (e.g. ssh://git@host/valerii/finances.git)")
BRANCH = os.environ.get("BRANCH", "main")
REPO_DIR = os.environ.get("REPO_DIR", "/repo")
SSH_KEY_PATH = os.environ.get("SSH_KEY_PATH", "/root/.ssh/id_rsa")
# OpenSSH refuses private keys with loose permissions ("UNPROTECTED PRIVATE
# KEY FILE"), and a read-only bind mount can't be chmod'd in place. Copy the
# key to a writable path with 0600 before building GIT_SSH_COMMAND.
if os.path.isfile(SSH_KEY_PATH):
    _key_copy = SSH_KEY_PATH + ".copy"
    os.makedirs(os.path.dirname(_key_copy), exist_ok=True)
    shutil.copyfile(SSH_KEY_PATH, _key_copy)
    os.chmod(_key_copy, 0o600)
    SSH_KEY_PATH = _key_copy
SSH_KNOWN_HOSTS = os.environ.get("SSH_KNOWN_HOSTS", "/root/.ssh/known_hosts")
FILE_PATTERNS = os.environ.get("FILE_PATTERNS", "*.bean *.jsonl")
GIT_NAME = os.environ.get("GIT_NAME", "Finances Writer")
GIT_EMAIL = os.environ.get("GIT_EMAIL", "writer@finances.local")
TICK_SLEEP = int(os.environ.get("TICK_SLEEP", "30"))
PATTERNS = FILE_PATTERNS.split()

ENV = dict(os.environ)
ENV.setdefault("PATH", "/usr/bin:/bin:/usr/local/bin")
ENV.update({
    "GIT_AUTHOR_NAME": GIT_NAME,
    "GIT_AUTHOR_EMAIL": GIT_EMAIL,
    "GIT_COMMITTER_NAME": GIT_NAME,
    "GIT_COMMITTER_EMAIL": GIT_EMAIL,
    "GIT_SSH_COMMAND": (
        f"ssh -i {SSH_KEY_PATH} -o IdentitiesOnly=yes "
        f"-o StrictHostKeyChecking=accept-new "
        f"-o UserKnownHostsFile={SSH_KNOWN_HOSTS}"
    ),
})

if __name__ == "__main__":
    main()
