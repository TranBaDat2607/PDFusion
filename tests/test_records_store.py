"""`storage/records.py`: the records database behind chat (#59).

`pdfusion.db` says which documents exist and which chat indexes they have; the
vector store is derived from it. What it has to guarantee: an index isn't ready
until it is complete, a document has one ready index per embedding model and
chunker, foreign keys hold, and an index a crash left half-built is recognised.

Every test opens its own database under `tmp_path`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from desktop_pdf_translator.storage.migrations import SchemaTooNewError
from desktop_pdf_translator.storage.records import RecordsStore

MODEL = "sentence-transformers/test-model"
DIMENSIONS = 384
CHUNKER = "1"
PAPER = "C:/papers/paper.pdf"


@pytest.fixture
def records(tmp_path: Path) -> RecordsStore:
    return RecordsStore(tmp_path / "pdfusion.db")


def _document(records: RecordsStore, document_id: str = "doc-a", path: str = PAPER) -> str:
    records.upsert_document(document_id, Path(path).name, 1234, path)
    return document_id


def _ready_index(
    records: RecordsStore, document_id: str, model: str = MODEL, chunker: str = CHUNKER
) -> str:
    index = records.begin_index(document_id, model, DIMENSIONS, chunker)
    records.complete_index(index.id, chunk_count=3)
    return index.id


def test_a_new_database_starts_at_the_current_schema_with_foreign_keys_on(
    records: RecordsStore,
):
    conn = records._conn()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_an_index_needs_a_document(records: RecordsStore):
    with pytest.raises(sqlite3.IntegrityError):
        records.begin_index("no-such-document", MODEL, DIMENSIONS, CHUNKER)


def test_an_index_is_not_ready_until_it_is_complete(records: RecordsStore):
    doc = _document(records)
    index = records.begin_index(doc, MODEL, DIMENSIONS, CHUNKER)

    assert index.status == "indexing"
    assert records.ready_index(doc, MODEL, CHUNKER) is None

    records.complete_index(index.id, chunk_count=7, page_count=2)

    ready = records.ready_index(doc, MODEL, CHUNKER)
    assert (ready.id, ready.status, ready.chunk_count) == (index.id, "ready", 7)
    assert records._conn().execute("SELECT page_count FROM documents").fetchone()[0] == 2


def test_an_index_built_with_another_model_or_chunker_does_not_count(
    records: RecordsStore,
):
    doc = _document(records)
    _ready_index(records, doc, model="an-older-model")

    assert records.ready_index(doc, MODEL, CHUNKER) is None
    assert records.ready_index(doc, "an-older-model", "2") is None


def test_completing_an_index_replaces_the_documents_other_indexes(records: RecordsStore):
    doc = _document(records)
    old = _ready_index(records, doc, model="an-older-model")
    new = records.begin_index(doc, MODEL, DIMENSIONS, CHUNKER)

    assert records.complete_index(new.id, chunk_count=4) == [old]

    assert records.index_ids() == {new.id}


def test_the_database_itself_allows_one_ready_index_per_model_and_chunker(
    records: RecordsStore,
):
    """The partial unique index is the guarantee, not `complete_index`'s
    cleanup — a second writer can't slip a duplicate past it."""
    doc = _document(records)
    first = _ready_index(records, doc)
    conn = records._conn()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO rag_indexes (id, document_id, embedding_model, embedding_dim, "
            "chunker_version, status, created_at) VALUES ('second', ?, ?, ?, ?, 'ready', 0)",
            (doc, MODEL, DIMENSIONS, CHUNKER),
        )
    conn.rollback()

    assert records.index_ids() == {first}


def test_a_failed_index_is_never_ready(records: RecordsStore):
    doc = _document(records)
    index = records.begin_index(doc, MODEL, DIMENSIONS, CHUNKER)

    records.fail_index(index.id, "the embedding model could not be downloaded")

    failed = records.get_index(index.id)
    assert (failed.status, failed.error) == (
        "failed",
        "the embedding model could not be downloaded",
    )
    assert records.ready_index(doc, MODEL, CHUNKER) is None
    with pytest.raises(LookupError):
        records.complete_index(index.id, chunk_count=3)


def test_one_file_opened_from_two_paths_is_one_document(records: RecordsStore):
    _document(records, "doc-a", "C:/downloads/paper.pdf")
    _document(records, "doc-a", "D:/backup/paper.pdf")
    conn = records._conn()

    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM document_locations").fetchone()[0] == 2
    assert records.document_path("doc-a") == "D:/backup/paper.pdf"
    assert records.document_path("unknown") is None


def test_deleting_a_documents_indexes_hands_back_their_ids(records: RecordsStore):
    doc = _document(records)
    index = _ready_index(records, doc)

    assert records.delete_document_indexes(doc) == [index]
    assert records.delete_document_indexes(doc) == []
    assert records.ready_index(doc, MODEL, CHUNKER) is None


def test_deleting_a_document_takes_its_locations_and_indexes_with_it(
    records: RecordsStore,
):
    doc = _document(records)
    _ready_index(records, doc)
    conn = records._conn()

    with conn:
        conn.execute("DELETE FROM documents WHERE id = ?", (doc,))

    assert conn.execute("SELECT COUNT(*) FROM document_locations").fetchone()[0] == 0
    assert records.index_ids() == set()


def test_indexes_a_crash_left_half_built_are_failed_on_recovery(records: RecordsStore):
    ready = _ready_index(records, _document(records))
    interrupted = records.begin_index(
        _document(records, "doc-b", "C:/papers/other.pdf"), MODEL, DIMENSIONS, CHUNKER
    )

    assert records.fail_stale_indexing() == [interrupted.id]

    assert records.get_index(interrupted.id).status == "failed"
    assert records.get_index(ready).status == "ready"
    assert records.fail_stale_indexing() == []


def test_records_from_a_newer_build_are_refused(tmp_path: Path):
    db = tmp_path / "pdfusion.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 9")
    conn.commit()
    conn.close()

    with pytest.raises(SchemaTooNewError):
        RecordsStore(db).index_ids()
