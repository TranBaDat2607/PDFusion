"""The records database: the documents the app knows, and their chat indexes.

`pdfusion.db`, under the app's data root, is the system of record (#59). A
document is identified by the SHA-256 of its bytes; the paths it has been
opened from are attributes of it, not its key. Each chat index over a document
is a row here, and its chunks live in a ChromaDB collection named after that
row (`rag/vector_store.py`) — derived data, which `api/routes/rag.py:_recover`
reconciles with these rows.

The caches keep files of their own (`translation_cache/`,
`translated_pdf_cache/`): they are disposable, and clearing or deleting them
must never touch a record. `pdf_translations.file_hash` is the same SHA-256 as
`documents.id`, so the two can be joined by value.

Stdlib-only.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set

from ..utils.paths import appdata_dir
from .migrations import Migration, migrate
from .sqlite import ThreadLocalConnections, now_ms


def _v1_documents_and_chat_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE documents (
            id              TEXT PRIMARY KEY,   -- sha256 of the file's bytes, hex
            display_name    TEXT NOT NULL,      -- file name it was last opened under
            size_bytes      INTEGER NOT NULL,
            page_count      INTEGER,            -- known once an index has read it
            created_at      INTEGER NOT NULL,   -- Unix ms, UTC
            last_opened_at  INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE document_locations (    -- one file can live at several paths
            document_id   TEXT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
            path          TEXT NOT NULL,
            last_seen_at  INTEGER NOT NULL,
            PRIMARY KEY (document_id, path)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE rag_indexes (
            id               TEXT PRIMARY KEY,  -- uuid4 hex; its collection is rag_<id>
            document_id      TEXT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
            embedding_model  TEXT NOT NULL,
            embedding_dim    INTEGER NOT NULL,
            chunker_version  TEXT NOT NULL,
            status           TEXT NOT NULL CHECK (status IN ('indexing', 'ready', 'failed')),
            chunk_count      INTEGER,
            error            TEXT,
            created_at       INTEGER NOT NULL,
            completed_at     INTEGER
        )
        """
    )
    conn.execute("CREATE INDEX rag_indexes_by_document ON rag_indexes (document_id)")
    # At most one ready index per document, embedding model and chunker, so
    # "is this PDF indexed?" has exactly one answer — enforced by the database,
    # not only by `complete_index` cleaning up.
    conn.execute(
        """
        CREATE UNIQUE INDEX rag_indexes_one_ready
            ON rag_indexes (document_id, embedding_model, chunker_version)
            WHERE status = 'ready'
        """
    )


_MIGRATIONS = (
    Migration(1, "documents and chat indexes", _v1_documents_and_chat_indexes),
)


@dataclass(frozen=True)
class IndexRecord:
    id: str
    document_id: str
    embedding_model: str
    embedding_dim: int
    chunker_version: str
    status: str  # 'indexing' | 'ready' | 'failed'
    chunk_count: Optional[int]
    error: Optional[str]
    created_at: int
    completed_at: Optional[int]


class RecordsStore:
    """`pdfusion.db`, safe to share between threads.

    Every method blocks; from async code, call it through `asyncio.to_thread`.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else appdata_dir() / "pdfusion.db"
        self._connections = ThreadLocalConnections(self.db_path)
        self._schema_ready = False
        self._schema_lock = threading.Lock()
        self._write_lock = threading.Lock()

    def _conn(self) -> sqlite3.Connection:
        if not self._schema_ready:
            with self._schema_lock:
                if not self._schema_ready:
                    # Records are never discarded to make room: a database
                    # written by a newer build refuses to open instead.
                    migrate(self.db_path, _MIGRATIONS, on_too_new="raise")
                    self._schema_ready = True
        return self._connections.get()

    # -- documents -----------------------------------------------------------

    def upsert_document(
        self, document_id: str, display_name: str, size_bytes: int, path: str
    ) -> None:
        """Record that the document was just opened from `path`."""
        now = now_ms()
        conn = self._conn()
        with self._write_lock, conn:
            conn.execute(
                """
                INSERT INTO documents (id, display_name, size_bytes, created_at, last_opened_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    display_name = excluded.display_name,
                    last_opened_at = excluded.last_opened_at
                """,
                (document_id, display_name, size_bytes, now, now),
            )
            conn.execute(
                """
                INSERT INTO document_locations (document_id, path, last_seen_at)
                VALUES (?, ?, ?)
                ON CONFLICT (document_id, path) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at
                """,
                (document_id, path, now),
            )

    def document_path(self, document_id: str) -> Optional[str]:
        """The path the document was most recently opened from."""
        row = self._conn().execute(
            "SELECT path FROM document_locations WHERE document_id = ? "
            "ORDER BY last_seen_at DESC, rowid DESC LIMIT 1",
            (document_id,),
        ).fetchone()
        return row["path"] if row else None

    # -- chat indexes --------------------------------------------------------

    def ready_index(
        self, document_id: str, embedding_model: str, chunker_version: str
    ) -> Optional[IndexRecord]:
        row = self._conn().execute(
            "SELECT * FROM rag_indexes WHERE document_id = ? AND embedding_model = ? "
            "AND chunker_version = ? AND status = 'ready'",
            (document_id, embedding_model, chunker_version),
        ).fetchone()
        return _index_record(row)

    def get_index(self, index_id: str) -> Optional[IndexRecord]:
        row = self._conn().execute(
            "SELECT * FROM rag_indexes WHERE id = ?", (index_id,)
        ).fetchone()
        return _index_record(row)

    def begin_index(
        self,
        document_id: str,
        embedding_model: str,
        embedding_dim: int,
        chunker_version: str,
    ) -> IndexRecord:
        """Record an index about to be built. Nothing reads it until
        `complete_index` marks it ready."""
        index_id = uuid.uuid4().hex
        conn = self._conn()
        with self._write_lock, conn:
            conn.execute(
                """
                INSERT INTO rag_indexes
                    (id, document_id, embedding_model, embedding_dim, chunker_version,
                     status, created_at)
                VALUES (?, ?, ?, ?, ?, 'indexing', ?)
                """,
                (index_id, document_id, embedding_model, embedding_dim, chunker_version, now_ms()),
            )
        record = self.get_index(index_id)
        assert record is not None
        return record

    def complete_index(
        self, index_id: str, chunk_count: int, page_count: Optional[int] = None
    ) -> List[str]:
        """Mark an index ready, replacing every other index of its document.

        One transaction, so a question never finds the document between its old
        index going and its new one arriving. Returns the replaced indexes' ids;
        dropping their collections is the caller's job.
        """
        conn = self._conn()
        with self._write_lock, conn:
            row = conn.execute(
                "SELECT document_id FROM rag_indexes WHERE id = ? AND status = 'indexing'",
                (index_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"no index {index_id} is being built")
            document_id = row["document_id"]
            replaced = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM rag_indexes WHERE document_id = ? AND id != ?",
                    (document_id, index_id),
                )
            ]
            conn.execute(
                "DELETE FROM rag_indexes WHERE document_id = ? AND id != ?",
                (document_id, index_id),
            )
            conn.execute(
                "UPDATE rag_indexes SET status = 'ready', chunk_count = ?, error = NULL, "
                "completed_at = ? WHERE id = ?",
                (chunk_count, now_ms(), index_id),
            )
            if page_count is not None:
                conn.execute(
                    "UPDATE documents SET page_count = ? WHERE id = ?",
                    (page_count, document_id),
                )
        return replaced

    def fail_index(self, index_id: str, error: str) -> None:
        conn = self._conn()
        with self._write_lock, conn:
            conn.execute(
                "UPDATE rag_indexes SET status = 'failed', error = ?, completed_at = ? "
                "WHERE id = ? AND status = 'indexing'",
                (error, now_ms(), index_id),
            )

    def fail_stale_indexing(self) -> List[str]:
        """Fail every index still marked `indexing`, and return their ids.

        Correct only before this process starts indexing: then every such row
        belongs to a process that died partway through an index.
        """
        conn = self._conn()
        with self._write_lock, conn:
            stale = [
                r["id"]
                for r in conn.execute("SELECT id FROM rag_indexes WHERE status = 'indexing'")
            ]
            conn.execute(
                "UPDATE rag_indexes SET status = 'failed', "
                "error = 'Interrupted before it finished', completed_at = ? "
                "WHERE status = 'indexing'",
                (now_ms(),),
            )
        return stale

    def delete_document_indexes(self, document_id: str) -> List[str]:
        """Delete every index of a document; return their ids, `[]` if none."""
        conn = self._conn()
        with self._write_lock, conn:
            ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM rag_indexes WHERE document_id = ?", (document_id,)
                )
            ]
            conn.execute("DELETE FROM rag_indexes WHERE document_id = ?", (document_id,))
        return ids

    def delete_index(self, index_id: str) -> bool:
        """Delete one index's row; `False` if it had none. Dropping its
        collection is the caller's job."""
        conn = self._conn()
        with self._write_lock, conn:
            cursor = conn.execute("DELETE FROM rag_indexes WHERE id = ?", (index_id,))
        return cursor.rowcount > 0

    def index_ids(self, status: Optional[str] = None) -> Set[str]:
        """The ids of every index, or of those with this status."""
        if status is None:
            rows = self._conn().execute("SELECT id FROM rag_indexes")
        else:
            rows = self._conn().execute(
                "SELECT id FROM rag_indexes WHERE status = ?", (status,)
            )
        return {r["id"] for r in rows}


def _index_record(row: Optional[sqlite3.Row]) -> Optional[IndexRecord]:
    return IndexRecord(**{key: row[key] for key in row.keys()}) if row else None


_INSTANCE: Optional[RecordsStore] = None
_INSTANCE_LOCK = threading.Lock()


def get_records_store() -> RecordsStore:
    """Process-wide singleton. Construction does no I/O: the database opens,
    and migrates, on first use."""
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = RecordsStore()
    return _INSTANCE
