"""`storage/migrations.py`: how every SQLite store changes its schema (#59).

What a runner like this owes the stores, one guarantee per test: steps run once,
in version order, each atomically with its version bump; a database from a
newer build is refused (records) or set aside (caches); and threads opening one
file at once don't migrate it twice.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import List, Set, Tuple

import pytest

from desktop_pdf_translator.storage.migrations import (
    Migration,
    SchemaTooNewError,
    migrate,
    schema_version,
)


def _create(version: int, table: str) -> Migration:
    return Migration(
        version,
        f"create {table}",
        lambda conn: conn.execute(f"CREATE TABLE {table} (id INTEGER)"),
    )


def _inspect(path: Path) -> Tuple[int, Set[str]]:
    conn = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        return schema_version(conn), tables
    finally:
        conn.close()


def _written_by_a_newer_build(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE future (id INTEGER)")
    conn.execute("PRAGMA user_version = 7")
    conn.commit()
    conn.close()


def test_a_new_database_gets_every_step(tmp_path: Path):
    db = tmp_path / "store.db"

    assert migrate(db, [_create(1, "a"), _create(2, "b")]) == 2

    assert _inspect(db) == (2, {"a", "b"})


def test_steps_run_in_version_order_not_list_order(tmp_path: Path):
    order: List[int] = []
    steps = [
        Migration(v, f"step {v}", lambda conn, v=v: order.append(v)) for v in (3, 1, 2)
    ]

    migrate(tmp_path / "store.db", steps)

    assert order == [1, 2, 3]


def test_a_current_database_runs_nothing(tmp_path: Path):
    db = tmp_path / "store.db"
    migrate(db, [_create(1, "a")])
    ran: List[int] = []

    assert migrate(db, [Migration(1, "again", lambda conn: ran.append(1))]) == 1

    assert ran == []


def test_an_older_database_runs_only_the_newer_steps(tmp_path: Path):
    db = tmp_path / "store.db"
    migrate(db, [_create(1, "a")])

    assert migrate(db, [_create(1, "a"), _create(2, "b")]) == 2

    assert _inspect(db) == (2, {"a", "b"})


def test_a_failing_step_takes_its_changes_and_its_version_with_it(tmp_path: Path):
    db = tmp_path / "store.db"

    def half_done(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE b (id INTEGER)")
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError, match="disk full"):
        migrate(db, [_create(1, "a"), Migration(2, "half done", half_done)])

    assert _inspect(db) == (1, {"a"})
    # Nothing records the failure, so the next open simply retries the step.
    assert migrate(db, [_create(1, "a"), _create(2, "b")]) == 2


def test_records_from_a_newer_build_are_refused_and_left_alone(tmp_path: Path):
    db = tmp_path / "records.db"
    _written_by_a_newer_build(db)

    with pytest.raises(SchemaTooNewError):
        migrate(db, [_create(1, "a")], on_too_new="raise")

    assert _inspect(db) == (7, {"future"})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["records.db"]


def test_a_cache_from_a_newer_build_is_set_aside_for_a_fresh_one(tmp_path: Path):
    db = tmp_path / "cache.db"
    _written_by_a_newer_build(db)

    assert migrate(db, [_create(1, "a")], on_too_new="reset") == 1

    assert _inspect(db) == (1, {"a"})
    (aside,) = [
        p for p in tmp_path.glob("cache.db.v7-*") if not p.name.endswith(("-wal", "-shm"))
    ]
    assert _inspect(aside) == (7, {"future"})


def test_threads_opening_one_database_at_once_migrate_it_once(tmp_path: Path):
    db = tmp_path / "store.db"
    applied: List[int] = []
    results: List[int] = []
    start_together = threading.Barrier(8)

    def slow_step(conn: sqlite3.Connection) -> None:
        applied.append(1)
        time.sleep(0.05)  # widen the window a second opener would race into
        conn.execute("CREATE TABLE a (id INTEGER)")

    def open_it() -> None:
        start_together.wait()
        results.append(migrate(db, [Migration(1, "slow", slow_step)]))

    threads = [threading.Thread(target=open_it) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert applied == [1]
    assert results == [1] * 8


def test_two_steps_claiming_one_version_is_a_programming_error(tmp_path: Path):
    with pytest.raises(ValueError):
        migrate(tmp_path / "store.db", [_create(1, "a"), _create(1, "b")])


def test_a_version_2_records_database_gains_provider_and_model_on_chat_messages(
    tmp_path: Path,
):
    """#87: a v2 database (documents, a ready index, saved chat messages) opens
    at version 3 with `chat_messages.provider`/`.model` added, every existing
    row kept, and its pre-existing rows reading back with both columns NULL."""
    from desktop_pdf_translator.storage import records as records_module

    db = tmp_path / "pdfusion.db"
    migrate(db, records_module._MIGRATIONS[:2])
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO documents (id, display_name, size_bytes, created_at, last_opened_at) "
        "VALUES ('doc-a', 'paper.pdf', 1234, 0, 0)"
    )
    conn.execute(
        "INSERT INTO chat_messages (document_id, role, content, created_at) "
        "VALUES ('doc-a', 'user', 'What is measured?', 0)"
    )
    conn.execute(
        "INSERT INTO chat_messages (document_id, role, content, answer_json, created_at) "
        "VALUES ('doc-a', 'assistant', 'It measures tensile strength.', '{}', 0)"
    )
    conn.commit()
    conn.close()

    version = migrate(db, records_module._MIGRATIONS)

    assert version == 3
    conn = sqlite3.connect(db)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(chat_messages)")}
    assert {"provider", "model"} <= columns
    rows = conn.execute(
        "SELECT role, provider, model FROM chat_messages WHERE document_id = 'doc-a' ORDER BY id"
    ).fetchall()
    conn.close()
    assert rows == [("user", None, None), ("assistant", None, None)]
