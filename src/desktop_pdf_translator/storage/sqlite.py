"""SQLite connections and timestamps, shared by every store.

One place decides the pragmas a connection runs with, so the stores can't drift
apart on them. Both caches used to carry their own copy of this code, and
neither enabled foreign keys (#59).

Stdlib-only.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# How long a connection waits for another's write lock before failing with
# "database is locked".
BUSY_TIMEOUT_SECONDS = 5.0


def connect(path: Path) -> sqlite3.Connection:
    """Open `path` with the pragmas every store relies on.

    * WAL, so readers never block the writer or each other.
    * `synchronous=NORMAL`: an app crash loses nothing, and a power cut can
      lose the last few commits but never corrupts the file — the right trade
      for caches and an index that can be rebuilt.
    * `foreign_keys=ON`, which SQLite leaves off on every new connection.
    * a busy timeout (`timeout=`), so contention waits instead of raising.

    The stdlib's default transaction handling is kept: DML opens a transaction
    implicitly and `commit()` ends it. Migrations take the write lock up front
    with an explicit `BEGIN IMMEDIATE`.
    """
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class ThreadLocalConnections:
    """One connection per thread, opened on first use and reused after.

    Every paragraph of a translation reads the paragraph cache from one of
    BabelDOC's worker threads; reconnecting per call would pay the open and the
    pragmas (~1-3 ms on Windows) hundreds of times per PDF.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._local = threading.local()

    def get(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = connect(self.path)
            self._local.conn = conn
        return conn


def now_ms() -> int:
    """The current time as Unix milliseconds, which are UTC by definition.

    Every store records time this way. They used to write naive local-time ISO
    strings, which jump by an hour across a DST change.
    """
    return time.time_ns() // 1_000_000


def ms_to_iso(ms: int) -> str:
    """Unix milliseconds as ISO-8601 with an explicit UTC offset, for the wire."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def local_iso_to_utc_ms(column: str) -> str:
    """A SQL expression converting a naive local-time ISO-8601 column to UTC ms.

    That is the format every store wrote before #59 (`datetime.now().isoformat()`).
    SQLite's `'utc'` modifier reads the value as local time — the assumption
    Python made writing it — and converts it. NULL and unparseable text both
    come out NULL.
    """
    return (
        f"CAST(ROUND((julianday({column}, 'utc') - 2440587.5) * 86400000.0) AS INTEGER)"
    )
