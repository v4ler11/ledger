#!/usr/bin/env python3
"""ledgerd writer -- single-writer container entrypoint.

Every hour at MM=59: stages and commits changes in $FILE_PATTERNS as
"<dd-mm-yyyy>-<hh>", pushes to $BRANCH.
Every day at 23:59: after the hourly pass, squashes each day in the pending
top-of-history run (including leftover hourly commits from previous days,
e.g. 01-01-2026-01 + 01-01-2026-02 -> 01-01-2026) into one
"<dd-mm-yyyy>" commit per day and force-pushes with lease.

Runs in the container timezone -- set TZ so the 23:59 boundary matches yours.

Env contract (defaults shown):
  REPO_URL         required   ssh://git@host/path/repo.git
  BRANCH           main
  REPO_DIR         /repo
  SSH_KEY_PATH     /root/.ssh/id_rsa
  SSH_KNOWN_HOSTS  /root/.ssh/known_hosts
  FILE_PATTERNS    "*.bean *.jsonl"
  GIT_NAME/GIT_EMAIL   commit identity
  PUSH_RETRIES     5
  PUSH_RETRY_SLEEP 30
  TICK_SLEEP       30   loop granularity (seconds)
"""

from __future__ import annotations

import datetime as dt
import os
import re
import signal
import subprocess
import sys
import time
from typing import Optional

SUBJECT_RE = re.compile(r"^(\d{2}-\d{2}-\d{4})(?:-\d{2})?$")


def date_of(subject: str) -> Optional[str]:
    """Bare date of a commit subject; None unless it is one of ours."""
    m = SUBJECT_RE.match(subject)
    return m.group(1) if m else None


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def run(*args: str, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], env=ENV, cwd=cwd,
                          capture_output=True, text=True, check=True)


def try_run(*args: str, cwd: Optional[str] = None) -> Optional[str]:
    """Git command that may legitimately fail; returns stdout or None."""
    r = subprocess.run(["git", *args], env=ENV, cwd=cwd,
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def ensure_repo() -> None:
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", REPO_DIR],
                   env=ENV, capture_output=True, text=True)
    if not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        if not os.path.isdir(REPO_DIR) or not os.listdir(REPO_DIR):
            log(f"cloning {REPO_URL} into {REPO_DIR}")
            run("clone", REPO_URL, REPO_DIR)
        else:
            log(f"initializing {REPO_DIR} from {REPO_URL}")
            run("init", "-b", BRANCH, cwd=REPO_DIR)
            run("remote", "add", "origin", REPO_URL, cwd=REPO_DIR)
            run("fetch", "origin", cwd=REPO_DIR)
            run("reset", "--hard", f"origin/{BRANCH}", cwd=REPO_DIR)


def has_changes() -> bool:
    r = subprocess.run(["git", "status", "--porcelain", "--", *PATTERNS],
                       env=ENV, capture_output=True, text=True)
    return bool(r.stdout.strip())


def push_with_retry(force: bool) -> bool:
    for attempt in range(1, PUSH_RETRIES + 1):
        args = ["push"] + (["--force-with-lease"] if force else []) + ["origin", BRANCH]
        r = subprocess.run(["git", *args], env=ENV, capture_output=True, text=True)
        if r.returncode == 0:
            log(f"push ok (attempt {attempt})")
            return True
        log(f"push failed (attempt {attempt}/{PUSH_RETRIES}), retrying in {PUSH_RETRY_SLEEP}s")
        time.sleep(PUSH_RETRY_SLEEP)
    return False


def commit_changes() -> None:
    try_run("fetch", "origin", BRANCH)
    if has_changes():
        run("add", "--", *PATTERNS)
        name = dt.datetime.now().strftime("%d-%m-%Y-%H")
        run("commit", "-m", name)
        log(f"committed '{name}'")
    else:
        log(f"no changes in [{FILE_PATTERNS}]")
    try_run("rebase", f"origin/{BRANCH}")
    ahead = try_run("rev-list", "--count", f"origin/{BRANCH}..HEAD")
    if ahead is not None and int(ahead.strip()) > 0:
        if not push_with_retry(False):
            log("push deferred; retried on next run")


def merge_pending() -> None:
    try_run("fetch", "origin", BRANCH)
    commit_changes()

    # contiguous run of our commits at the top of history (newest first)
    subjects: list[str] = []
    k = 0
    while True:
        s = try_run("log", "-1", f"--skip={k}", "--pretty=%s")
        if s is None:
            break
        s = s.strip()
        if not date_of(s):
            break
        subjects.append(s)
        k += 1
    n = len(subjects)
    if n == 0 or all(date_of(s) == s for s in subjects):
        log("nothing to squash")
        return

    # one group per date, newest first: (date, tree at group's newest, authors ts)
    groups: list[tuple[str, str, str]] = []
    lastdate: Optional[str] = None
    for k, s in enumerate(subjects):
        d = date_of(s)
        assert d is not None
        if d != lastdate:
            tree = try_run("rev-parse", f"HEAD~{k}^{{tree}}")
            ts = try_run("show", "-s", "--format=%at", f"HEAD~{k}")
            groups.append((d, tree.strip(), ts.strip()))
            lastdate = d

    base = try_run("rev-parse", f"HEAD~{n}")  # anchor below the run; None at root
    base_date: Optional[str] = None
    if base is not None:
        bs = try_run("log", "-1", "--pretty=%s", base.strip())
        base_date = date_of(bs.strip()) if bs is not None else None

    newtip: Optional[str] = None
    created = 0
    for i, (d, tree, ts) in enumerate(reversed(groups)):
        if i == 0:
            if base is not None and d == base_date:
                parent = try_run("rev-parse", f"{base.strip()}^")
            else:
                parent = base.strip() if base is not None else None
        else:
            parent = newtip
        env = dict(ENV)
        env["GIT_AUTHOR_DATE"] = f"@{ts}"
        env["GIT_COMMITTER_DATE"] = f"@{ts}"
        args = ["commit-tree", tree, "-m", d]
        if parent:
            args += ["-p", parent]
        r = subprocess.run(["git", *args], env=env, capture_output=True, text=True, check=True)
        newtip = r.stdout.strip()
        created += 1

    if created > 0 and newtip is not None:
        run("reset", "--soft", newtip)
        log(f"squashed {created} day(s) ({n} commits) into daily commits; force-pushing")
        if not push_with_retry(True):
            log("merge push deferred; retried on next run")
    else:
        log("nothing to squash")


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    ensure_repo()
    os.chdir(REPO_DIR)
    log("startup catch-up run")
    try:
        commit_changes()
        merge_pending()
    except Exception as e:  # keep the loop alive (parity with shell `|| true`)
        log(f"startup step failed: {e}")
    while True:
        now = dt.datetime.now()
        if now.minute == 59:
            try:
                commit_changes()
                if now.hour == 23:
                    merge_pending()
            except Exception as e:
                log(f"scheduled step failed: {e}")
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