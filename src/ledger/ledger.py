"""Ledger lifecycle: loading, include-aware caching, and bean-check.

No watcher: the program assumes ledger files are not modified by hand
while it runs, so cache invalidation happens lazily — the connection is
reloaded only when a stale mtime/size fingerprint is observed on the
next access.
"""

import os
import threading
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import beanquery
from beancount import loader

LedgerError = Dict[str, object]
FileState = Dict[Path, Optional[Tuple[int, int]]]


def _format_loader_error(err) -> LedgerError:
    source = getattr(err, "source", None)
    if source:
        filename = source.get("filename")
        line = source.get("lineno")
    else:
        filename = None
        line = None

    return {
        "file": filename,
        "line": line,
        "type": type(err).__name__,
        "message": getattr(err, "message", str(err)),
    }


class LedgerManager:
    def __init__(self, ledger_path: Path) -> None:
        self._path = Path(ledger_path)
        self._conn: Optional[beanquery.Connection] = None
        self._file_state: Optional[FileState] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def connection(self) -> beanquery.Connection:
        """Return a cached beanquery Connection, reloading if the file has changed."""
        with self._lock:
            if self._stale():
                self._conn = beanquery.connect(
                    "beancount:" + self._path.absolute().as_posix()
                )
                self._file_state = self._connection_file_state(self._conn)
            assert self._conn is not None
            return self._conn

    def check(self) -> List[LedgerError]:
        """Run beancount.loader on the ledger and return structured errors."""
        _, errors, _ = loader.load_file(str(self._path))
        return [_format_loader_error(err) for err in errors]

    def connection_errors(self) -> List[LedgerError]:
        """Return loader errors from the cached beanquery connection."""
        conn = self.connection()
        return [_format_loader_error(err) for err in getattr(conn, "errors", [])]

    def invalidate(self) -> None:
        """Force the next connection() call to reload from disk."""
        with self._lock:
            self._conn = None
            self._file_state = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _file_fingerprint(self, path: Path) -> Optional[Tuple[int, int]]:
        try:
            stat = os.stat(path)
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _paths_for_connection(self, conn: beanquery.Connection) -> Set[Path]:
        includes = conn.options.get("include") or []
        paths = {self._path}
        base = self._path.parent
        paths.update(base / Path(path) for path in includes)
        return paths

    def _file_state_for(self, paths: Set[Path]) -> FileState:
        return {path: self._file_fingerprint(path) for path in paths}

    def _connection_file_state(self, conn: beanquery.Connection) -> FileState:
        return self._file_state_for(self._paths_for_connection(conn))

    def _stale(self) -> bool:
        if self._conn is None or self._file_state is None:
            return True
        return self._file_state_for(set(self._file_state)) != self._file_state