"""The records database: the documents the app knows, their chat indexes, and
what was said about them.

`pdfusion.db`, under the app's data root, is the system of record (#59). A
document is identified by the SHA-256 of its bytes; the paths it has been
opened from are attributes of it, not its key. Each chat index over a document
is a row here, and its chunks live in a ChromaDB collection named after that
row (`rag/vector_store.py`) — derived data, which `api/routes/rag.py:_recover`
reconciles with these rows. A document's chat history is kept here too (#31).

The caches keep files of their own (`translation_cache/`,
`translated_pdf_cache/`): they are disposable, and clearing or deleting them
must never touch a record. `pdf_translations.file_hash` is the same SHA-256 as
`documents.id`, so the two can be joined by value.

Stdlib-only.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

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


def _v2_chat_messages(conn: sqlite3.Connection) -> None:
    # A message belongs to a document, not to one of its indexes:
    # `complete_index` deletes a document's other indexes, so history keyed by
    # index would vanish whenever `rag/index_spec.py` changed. The page numbers
    # in a saved answer stay right, because a document's id is the hash of its
    # bytes. The `chunk_id`s in it name chunks of the index it was answered
    # from, and nothing may rely on them.
    conn.execute(
        """
        CREATE TABLE chat_messages (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id  TEXT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
            role         TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content      TEXT NOT NULL,   -- the question, or the answer's text
            answer_json  TEXT,            -- assistant only: the answer as sent, citations included
            created_at   INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX chat_messages_by_document ON chat_messages (document_id, id)"
    )


_MIGRATIONS = (
    Migration(1, "documents and chat indexes", _v1_documents_and_chat_indexes),
    Migration(2, "chat history", _v2_chat_messages),
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


@dataclass(frozen=True)
class ChatMessageRecord:
    id: int
    document_id: str
    role: str  # 'user' | 'assistant'
    content: str
    answer: Optional[Dict[str, Any]]  # assistant only: the answer as it was sent
    created_at: int


@dataclass(frozen=True)
class DocumentSummary:
    """A recorded document, as Settings lists it. Metadata only."""

    id: str
    display_name: str
    size_bytes: int
    page_count: Optional[int]
    last_opened_at: int
    path: Optional[str]  # where it was most recently opened from
    chunk_count: Optional[int]  # of its ready index, `None` without one
    question_count: int


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

    def list_documents(
        self, embedding_model: str, chunker_version: str
    ) -> List[DocumentSummary]:
        """Every recorded document, most recently opened first.

        `chunk_count` is that of the document's ready index for this embedding
        model and chunker, the one a question would use; `None` without one.
        """
        rows = self._conn().execute(
            """
            SELECT d.id, d.display_name, d.size_bytes, d.page_count, d.last_opened_at,
                   (SELECT l.path FROM document_locations l
                     WHERE l.document_id = d.id
                     ORDER BY l.last_seen_at DESC, l.rowid DESC LIMIT 1) AS path,
                   i.chunk_count,
                   (SELECT COUNT(*) FROM chat_messages m
                     WHERE m.document_id = d.id AND m.role = 'user') AS question_count
              FROM documents d
              LEFT JOIN rag_indexes i
                ON i.document_id = d.id AND i.status = 'ready'
               AND i.embedding_model = ? AND i.chunker_version = ?
             ORDER BY d.last_opened_at DESC, d.rowid DESC
            """,
            (embedding_model, chunker_version),
        ).fetchall()
        return [DocumentSummary(**{key: row[key] for key in row.keys()}) for row in rows]

    def delete_document(self, document_id: str) -> Optional[List[str]]:
        """Forget a document: its record, the paths it was opened from, its chat
        indexes and its chat history, in one transaction.

        Returns the deleted indexes' ids, whose collections are the caller's to
        drop, or `None` when the document isn't recorded.
        """
        conn = self._conn()
        with self._write_lock, conn:
            index_ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM rag_indexes WHERE document_id = ?", (document_id,)
                )
            ]
            cursor = conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return index_ids if cursor.rowcount else None

    # -- chat history --------------------------------------------------------

    def add_exchange(
        self, document_id: str, question: str, answer: Dict[str, Any]
    ) -> None:
        """Record a question and its answer, both or neither.

        Raises `sqlite3.IntegrityError` when the document isn't recorded, which
        happens when it was removed while the question was being answered.
        """
        now = now_ms()
        conn = self._conn()
        with self._write_lock, conn:
            conn.execute(
                "INSERT INTO chat_messages (document_id, role, content, created_at) "
                "VALUES (?, 'user', ?, ?)",
                (document_id, question, now),
            )
            conn.execute(
                "INSERT INTO chat_messages "
                "(document_id, role, content, answer_json, created_at) "
                "VALUES (?, 'assistant', ?, ?, ?)",
                (document_id, answer.get("answer", ""), json.dumps(answer, default=str), now),
            )

    def messages(self, document_id: str) -> List[ChatMessageRecord]:
        """A document's chat history, oldest first."""
        rows = self._conn().execute(
            "SELECT * FROM chat_messages WHERE document_id = ? ORDER BY id",
            (document_id,),
        ).fetchall()
        return [
            ChatMessageRecord(
                id=row["id"],
                document_id=row["document_id"],
                role=row["role"],
                content=row["content"],
                answer=json.loads(row["answer_json"]) if row["answer_json"] else None,
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def clear_messages(self, document_id: str) -> int:
        """Delete a document's chat history; return how many messages went."""
        conn = self._conn()
        with self._write_lock, conn:
            cursor = conn.execute(
                "DELETE FROM chat_messages WHERE document_id = ?", (document_id,)
            )
        return cursor.rowcount

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

    def delete_settled_indexes(self) -> List[str]:
        """Delete every index that isn't being built; return their ids.

        An index still `indexing` belongs to a job in flight, which completes
        or fails it. Documents and chat history stay.
        """
        conn = self._conn()
        with self._write_lock, conn:
            ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM rag_indexes WHERE status != 'indexing'"
                )
            ]
            conn.execute("DELETE FROM rag_indexes WHERE status != 'indexing'")
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
