"""Versioned, forward-only schema migrations for the SQLite stores.

A database records its schema version in `PRAGMA user_version`: an integer in
the file header, 0 until something sets it. `migrate` brings a database up to
the newest version it is given, one step at a time. Each step runs in its own
`BEGIN IMMEDIATE` transaction together with its version bump, so a step that
fails rolls back its version as well, and the next open retries it.

Before this, the stores added columns with an `ALTER TABLE` whose error was
swallowed, and nothing in a file said which shape it was in (#59).

A step's `apply` must not call `executescript()`: that commits any open
transaction before it runs, which would split the step from its version bump.

Stdlib-only.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Literal, Sequence

from .sqlite import BUSY_TIMEOUT_SECONDS, connect

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


class SchemaTooNewError(RuntimeError):
    """The database was written by a newer build than this one."""


_path_locks: Dict[str, threading.Lock] = {}
_path_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    # Serializes threads in this process. Another process is kept out by
    # `BEGIN IMMEDIATE` and the version re-read under it.
    key = os.path.normcase(str(path.resolve()))
    with _path_locks_guard:
        return _path_locks.setdefault(key, threading.Lock())


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(
    path: Path,
    migrations: Sequence[Migration],
    *,
    on_too_new: Literal["raise", "reset"] = "raise",
) -> int:
    """Bring the database at `path` up to date, and return its version.

    Cheap on every open: a current database costs one PRAGMA read.

    `on_too_new` covers a database newer than any step here — written by a
    build that has since been rolled back. `"raise"` suits records, which must
    never be discarded silently. `"reset"` suits caches: the file is moved
    aside and this build starts an empty one.
    """
    steps = sorted(migrations, key=lambda m: m.version)
    versions = [m.version for m in steps]
    if len(set(versions)) != len(versions) or any(v < 1 for v in versions):
        raise ValueError(f"migration versions must be unique and at least 1: {versions}")
    latest = versions[-1] if versions else 0

    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_for(path):
        # Read the version before `connect` switches the file to WAL: a
        # database from a newer build is refused, or set aside, untouched.
        probe = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
        try:
            current = schema_version(probe)
        finally:
            probe.close()

        if current > latest:
            if on_too_new == "raise":
                raise SchemaTooNewError(
                    f"{path} is at schema version {current}; this build knows "
                    f"versions up to {latest}"
                )
            _move_aside(path, current)
            current = 0

        conn = connect(path)
        try:
            for step in steps:
                if step.version <= current:
                    continue
                started = time.perf_counter()
                conn.execute("BEGIN IMMEDIATE")
                try:
                    # Another process may have migrated between the read above
                    # and this write lock.
                    current = schema_version(conn)
                    if step.version <= current:
                        conn.rollback()
                        continue
                    step.apply(conn)
                    conn.execute(f"PRAGMA user_version = {int(step.version)}")
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                current = step.version
                logger.info(
                    "%s: migrated to schema version %d (%s) in %.2fs",
                    path.name, step.version, step.name, time.perf_counter() - started,
                )
            return current
        finally:
            conn.close()


def _move_aside(path: Path, version: int) -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        source = path.with_name(path.name + suffix)
        if source.exists():
            os.replace(source, path.with_name(f"{path.name}.v{version}-{stamp}{suffix}"))
    logger.warning(
        "%s was written by a newer build (schema version %d); moved it aside "
        "and started an empty one",
        path.name, version,
    )
