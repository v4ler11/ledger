"""Canonical ledger layout + write-path guard.

The finance ledgers split directives into ``main`` (options+includes),
``accounts``, ``commodities``, and ``ledgers/<YEAR>`` files.
``resolve_target`` routes a new directive to the right file;
``validate_append`` proves an append keeps the ledger clean *before*
anything is written (and hands back the ``include`` line needed to make
a brand-new yearly file reachable from the root).
"""

import glob, os, re, shutil, tempfile
from datetime import date as _date
from pathlib import Path
from typing import List, Optional, Tuple, Union

from beancount import loader

from .ledger import LedgerError, _format_loader_error

KIND_ACCOUNT = "account"
KIND_COMMODITY = "commodity"
KIND_TRANSACTION = "transaction"

_ACCOUNTS = ("accounts.bean", "accounts.beancount", "accounts")
_COMMODITIES = ("commodities.bean", "commodities.beancount", "commodities")
_EXT = (".bean", ".beancount")
_SKIP = frozenset({".git", ".idea", ".venv", "__pycache__", ".pytest_cache", ".svn"})

_INCLUDE_RE = re.compile(r'^\s*include\s+"([^"]+)"')


def resolve_target(
    root: Union[str, Path], kind: str, date: Optional[_date] = None
) -> Path:
    """File a new ``kind`` directive should be appended to.

    If ``root`` is already the kind's own file (``accounts.bean``), use it
    as-is. Else look in ``root``'s dir for the split file/dir — or a
    ``ledgers``/``ledger`` dir for dated directives — and fall back to
    ``root`` itself for single-file ledgers.
    """
    root = Path(root).expanduser().resolve()
    base = root.parent
    if (kind == KIND_ACCOUNT and root.name in _ACCOUNTS) or (
        kind == KIND_COMMODITY and root.name in _COMMODITIES
    ):
        return root
    if kind == KIND_ACCOUNT:
        found = _find(base, _ACCOUNTS)
        return _file(found) if found is not None else root
    if kind == KIND_COMMODITY:
        found = _find(base, _COMMODITIES)
        return _file(found) if found is not None else root
    if kind == KIND_TRANSACTION and date is not None:
        for name in ("ledgers", "ledger"):
            d = base / name
            if d.is_dir():
                return _year_file(d, date.year)
    return root


def validate_append(
    root: Union[str, Path], target: Union[str, Path], rendered: str
) -> Tuple[List[LedgerError], Optional[str]]:
    """Return ``(errors, include_line)`` for appending ``rendered`` to ``target``.

    Stages a throwaway copy of the ledger, applies the append (+ any needed
    ``include``), and re-parses the whole tree. Empty ``errors`` means it's
    safe to persist; ``include_line`` is what the caller must also append to
    the root so ``target`` is actually loaded (``None`` if already reached).
    A brand-new (nonexistent) ledger can't be validated — considered safe so
    the first write bootstraps it. Nothing on disk is modified.
    """
    root = Path(root).expanduser().resolve()
    target = Path(target).expanduser().resolve()
    base = root.parent
    if not root.exists():
        return [], None

    include_line = _required_include(root, target)
    with tempfile.TemporaryDirectory(prefix="ledger-check-") as tmp:
        staged = Path(tmp) / "ledger"
        shutil.copytree(base, staged, ignore=shutil.ignore_patterns(*_SKIP), symlinks=True)
        with (staged / target.relative_to(base)).open("a", encoding="utf-8") as f:
            f.write(rendered)
        if include_line is not None:
            with (staged / root.name).open("a", encoding="utf-8") as f:
                f.write(include_line)

        _, errors, _ = loader.load_file(str(staged / root.name))
        return _real_errors(
            (_format_loader_error(e) for e in errors), staged, base
        ), include_line


def append_include(root: Union[str, Path], include_line: str) -> None:
    """Append an ``include`` line to the root ledger file (write path)."""
    root = Path(root).expanduser().resolve()
    with root.open("a", encoding="utf-8") as f:
        f.write(include_line)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _find(base: Path, names) -> Optional[Path]:
    for name in names:
        p = base / name
        if p.exists():
            return p
    return None


def _file(p: Path) -> Path:
    if not p.is_dir():
        return p
    return next((c for c in p.iterdir() if c.suffix in _EXT), p / "index.bean")


def _year_file(d: Path, year: int) -> Path:
    for ext in _EXT:
        p = d / f"{year}{ext}"
        if p.exists():
            return p
    ext = next((s.suffix for s in sorted(d.iterdir()) if s.suffix in _EXT), _EXT[0])
    return d / f"{year}{ext}"


def _real_errors(errors, staged: Path, base: Path) -> List[LedgerError]:
    """Map error paths from the staged copy back onto the real tree."""
    prefix = os.fspath(staged) + os.sep
    out = []
    for err in errors:
        f = err.get("file")
        if isinstance(f, str) and f.startswith(prefix):
            err = {**err, "file": str(base / f[len(prefix):])}
        out.append(err)
    return out


def _required_include(root: Path, target: Path) -> Optional[str]:
    """An ``include`` line needed so ``target`` is loaded, else ``None``.

    ``None`` when the root already loads ``target`` — literally or through
    an include glob — so a duplicate ``include`` (which beancount rejects)
    is never emitted.
    """
    _, _, options = loader.load_file(str(root))
    loaded = {Path(p).resolve() for p in options.get("include", [])}
    if target.resolve() in loaded:
        return None
    base = root.parent
    for line in root.read_text(encoding="utf-8").splitlines():
        m = _INCLUDE_RE.match(line)
        if m:
            pattern = m.group(1)
            if not os.path.isabs(pattern):
                pattern = str(base / pattern)
            if any(Path(p).resolve() == target.resolve() for p in glob.glob(pattern)):
                return None
    return f'include "{target.relative_to(base)}"\n'
