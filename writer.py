#!/usr/bin/env python3
"""ledgerd writer -- single-writer container entrypoint.

Scheduling (edge-triggered, no level checks):
  * hourly: once per hour, first observed tick at minute 59 -> commit
    changes to $FILE_PATTERNS as "<dd-mm-yyyy>-<hh>", push.
  * daily: once per day, first observed tick at/after 23:59 -> squash
    each pending day in the top-of-history run into one "<dd-mm-yyyy>"
    commit per day (including leftover hourly commits from previous
    days, e.g. 01-01-2026-01 + 01-01-2026-02 -> 01-01-2026) and
    force-push with lease.
  * recovery: if a daily window was missed (busy/slow tick, restart),
    the next tick outside the window merges pending days strictly older
    than today; a mid-day startup also merges only days < today, so
    today's accumulating hourlies are left alone until 23:59.

Only commits carrying the trailer "X-Ledger-Writer: true" are ever
considered writer commits; a manual commit whose subject happens to look
like "dd-mm-yyyy" is never absorbed.

Env contract (defaults shown):
  REPO_URL         required   ssh://git@host/path/repo.git
  BRANCH           main
  REPO_DIR         /repo
  SSH_KEY_PATH     /root/.ssh/id_rsa
  SSH_KNOWN_HOSTS  /root/.ssh/known_hosts
  FILE_PATTERNS    "*.bean *.jsonl"   (whitespace-separated; no spaces in names)
  GIT_NAME/GIT_EMAIL   commit identity
  PUSH_RETRIES     5
  PUSH_RETRY_SLEEP 30
  TICK_SLEEP       30   loop granularity (seconds)
"""

from __future__ import annotations

import datetime as dt
import fcntl
import fnmatch
import os
import re
import signal
import subprocess
import sys
import time
from typing import Optional

SUBJECT_RE = re.compile(r"^(\d{2}-\d{2}-\d{4})(?:-\d{2})?$")
TRAILER = "X-Ledger-Writer: true"


def date_of(subject: str) -> Optional[str]:
    """Bare date of a commit subject; None unless it matches our format."""
    m = SUBJECT_RE.match(subject)
    return m.group(1) if m else None


def ours(pretty: str) -> Optional[str]:
    """Bare date if this commit is ours (subject format AND trailer)."""
    lines = pretty.splitlines()
    if not lines:
        return None
    subj = lines[0].strip()
    if TRAILER not in "\n".join(lines[1:]):
        return None
    return date_of(subj)


def daily_window(now: dt.datetime) -> bool:
    return (now.hour, now.minute) >= (23, 59)


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def log_critical(msg: str) -> None:
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] CRITICAL: {msg}",
          file=sys.stderr, flush=True)


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


def acquire_lock() -> None:
    # One ledgerd process (writer or reader) per REPO_DIR; readers flock the
    # same file. Intended deployment gives each container its own volume, so
    # contention means misconfiguration -- fail fast instead of racing.
    fd = open(os.path.join(REPO_DIR, ".ledgerd.lock"), "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("another ledgerd-writer already holds the lock on this REPO_DIR")
    global LOCK_FD
    LOCK_FD = fd


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


def push(force: bool) -> bool:
    args = ["push"] + (["--force-with-lease"] if force else []) + ["origin", BRANCH]
    r = git(*args)
    if r.returncode == 0:
        log(f"push ok")
        return True
    log(f"push failed: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'unknown'}")
    return False


def rebase_onto_remote() -> bool:
    """Rebase onto origin/$BRANCH; on conflict abort and report. No broken state."""
    if try_run("rev-parse", "--verify", f"origin/{BRANCH}") is None:
        return True  # no upstream yet (fresh empty remote); plain push creates it
    r = git("rebase", f"origin/{BRANCH}")
    if r.returncode == 0:
        return True
    log_critical(f"rebase onto origin/{BRANCH} failed: {r.stderr.strip()}")
    subprocess.run(["git", "rebase", "--abort"], env=ENV, capture_output=True, text=True)
    return False


def commit_changes() -> None:
    try_run("fetch", "origin", BRANCH)
    if has_changes():
        files = matching_files()
        run("add", "--", *files)
        name = dt.datetime.now().strftime("%d-%m-%Y-%H")
        run("commit", "-m", name, "-m", TRAILER)
        log(f"committed '{name}'")
    else:
        log(f"no changes in [{FILE_PATTERNS}]")
    if try_run("rev-parse", "--verify", f"origin/{BRANCH}") is None:
        # fresh empty remote: no upstream to rebase/ahead-count against;
        # direct push creates the branch on the remote
        if try_run("rev-parse", "--verify", "HEAD") is not None:
            push(False)
        return
    if not rebase_onto_remote():
        return
    for attempt in range(1, PUSH_RETRIES + 1):
        ahead = try_run("rev-list", "--count", f"origin/{BRANCH}..HEAD")
        if ahead is None or int(ahead.strip()) == 0:
            return
        if push(False):
            return
        time.sleep(PUSH_RETRY_SLEEP)
        try_run("fetch", "origin", BRANCH)
        if not rebase_onto_remote():
            return
    log("push deferred; retried on next run")


def scan_run() -> list[tuple[str, str, str]]:
    """Contiguous top-of-history run of our commits, newest-first:
    (subject, tree oid, author timestamp)."""
    entries: list[tuple[str, str, str]] = []
    k = 0
    while True:
        pretty = try_run("log", "-1", f"--skip={k}", "--pretty=%B")
        if pretty is None or ours(pretty) is None:
            break
        subj = pretty.splitlines()[0].strip()
        tree = try_run("rev-parse", f"HEAD~{k}^{{tree}}").strip()
        ts = try_run("show", "-s", "--format=%at", f"HEAD~{k}").strip()
        entries.append((subj, tree, ts))
        k += 1
    return entries


def commit_tree(tree: str, parent: Optional[str], msg: str, ts: str) -> str:
    env = dict(ENV)
    env["GIT_AUTHOR_DATE"] = f"@{ts}"
    env["GIT_COMMITTER_DATE"] = f"@{ts}"
    args = ["commit-tree", tree, "-m", msg, "-m", TRAILER]
    if parent:
        args += ["-p", parent]
    r = subprocess.run(["git", *args], env=env, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def merge_pending(merge_today: bool) -> None:
    """Squash pending days. merge_today=False leaves today's hourlies alone
    (used for startup catch-up and missed-window recovery)."""
    try_run("fetch", "origin", BRANCH)
    commit_changes()
    # a rebase conflict leaves local history divergent from origin; merging
    # (and force-pushing) that state could destroy remote commits -- refuse.
    # (no upstream yet: nothing to diverge from, let the merge proceed)
    if (try_run("rev-parse", "--verify", f"origin/{BRANCH}") is not None
            and try_run("merge-base", "--is-ancestor", f"origin/{BRANCH}", "HEAD") is None):
        log_critical(
            f"local history diverged from origin/{BRANCH} (rebase conflict or "
            "foreign push); skipping merge until resolved"
        )
        return

    entries = scan_run()
    n = len(entries)
    if n == 0:
        log("nothing to squash")
        return
    today = dt.date.today().strftime("%d-%m-%Y")

    def mergeable(subj: str) -> bool:
        d = date_of(subj)
        return d is not None and (d < today or (d == today and merge_today))

    mergeable_idx = [k for k in range(n) if mergeable(entries[k][0])]
    if not mergeable_idx:
        log("nothing to squash")
        return
    k_min = min(mergeable_idx)
    # region [k_min, n) may contain already-merged daily commits (e.g. restart
    # with today's hourlies on top of merged older days): merging them again
    # would rewrite the kept block above for no content change -- skip.
    if all(entries[k][0] == date_of(entries[k][0]) for k in range(k_min, n)):
        log("nothing to squash")
        return

    # anchor below the run; None if the run reaches the root
    base = try_run("rev-parse", f"HEAD~{n}")
    base_date = None
    if base is not None:
        bpretty = try_run("log", "-1", "--pretty=%B", base.strip())
        if bpretty is not None:
            base_date = ours(bpretty)

    # groups: mergeable positions [k_min, n) collapsed per date, oldest first
    groups: list[tuple[str, str, str]] = []
    i = n - 1
    while i >= k_min:
        d = date_of(entries[i][0])
        j = i
        while j - 1 >= k_min and date_of(entries[j - 1][0]) == d:
            j -= 1
        groups.append((d, entries[j][1], entries[j][2]))  # tree/ts at group's newest
        i = j - 1

    parent: Optional[str] = None
    if base is not None:
        parent = base.strip()
        if base_date is not None and groups[0][0] == base_date:
            # oldest merged day replaces base (same day) instead of stacking
            pp = try_run("rev-parse", f"{base.strip()}^")
            if pp is not None:
                parent = pp.strip()

    newtip: Optional[str] = None
    for d, tree, ts in groups:
        newtip = commit_tree(tree, parent, d, ts)
        parent = newtip
    # kept block [0, k_min) re-created individually on top (today's hourlies)
    for kk in range(k_min - 1, -1, -1):
        subj, tree, ts = entries[kk]
        newtip = commit_tree(tree, newtip, subj, ts)

    if newtip is None:
        log("nothing to squash")
        return
    run("reset", "--soft", newtip)
    log(f"squashed {len(groups)} day(s) ({n} commits) into daily commits; force-pushing")
    # lease-rejected force-push = external writer: fail loudly, never blind-retry
    if not push(True):
        log_critical(
            f"--force-with-lease rejected on origin/{BRANCH}: someone pushed meanwhile. "
            "Not retrying blindly; will re-attempt at the next scheduled merge."
        )


# --- main loop --------------------------------------------------------------

def main() -> None:
    def _bye(*_: object) -> None:
        sys.exit(0)
    signal.signal(signal.SIGTERM, _bye)

    ensure_repo()
    os.chdir(REPO_DIR)
    acquire_lock()

    now = dt.datetime.now()
    last_hourly = now.strftime("%Y-%m-%d-%H")

    log("startup catch-up run")
    try:
        commit_changes()
        merge_pending(daily_window(now))
        last_daily = now.strftime("%Y-%m-%d")
    except Exception as e:
        log_critical(f"startup step failed: {e}")
        last_daily = None  # window/recovery below will retry

    while True:
        now = dt.datetime.now()
        slot_hourly = now.strftime("%Y-%m-%d-%H")
        slot_daily = now.strftime("%Y-%m-%d")
        try:
            if now.minute == 59 and last_hourly != slot_hourly:
                commit_changes()
                last_hourly = slot_hourly
            if daily_window(now) and last_daily != slot_daily:
                merge_pending(True)
                last_daily = slot_daily
            elif (not daily_window(now) and last_daily is not None
                  and last_daily < slot_daily):
                # missed (or deferred) daily merge: recover old days only
                merge_pending(False)
                last_daily = slot_daily
        except Exception as e:
            log_critical(f"scheduled step failed: {e}")
        time.sleep(TICK_SLEEP)


# --- config / env -----------------------------------------------------------

REPO_URL = os.environ.get("REPO_URL", "")
if not REPO_URL:
    sys.exit("REPO_URL is required (e.g. ssh://git@host/valerii/finances.git)")
BRANCH = os.environ.get("BRANCH", "main")
REPO_DIR = os.environ.get("REPO_DIR", "/repo")
SSH_KEY_PATH = os.environ.get("SSH_KEY_PATH", "/root/.ssh/id_rsa")
SSH_KNOWN_HOSTS = os.environ.get("SSH_KNOWN_HOSTS", "/root/.ssh/known_hosts")
FILE_PATTERNS = os.environ.get("FILE_PATTERNS", "*.bean *.jsonl")
GIT_NAME = os.environ.get("GIT_NAME", "Finances Writer")
GIT_EMAIL = os.environ.get("GIT_EMAIL", "writer@finances.local")
PUSH_RETRIES = int(os.environ.get("PUSH_RETRIES", "5"))
PUSH_RETRY_SLEEP = int(os.environ.get("PUSH_RETRY_SLEEP", "30"))
TICK_SLEEP = int(os.environ.get("TICK_SLEEP", "30"))
PATTERNS = FILE_PATTERNS.split()
LOCK_FD = None

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