"""`storage/records.py`: the records database behind chat (#59, #31).

`pdfusion.db` says which documents exist, which chat indexes they have, and
what was asked about them; the vector store is derived from it. What it has to
guarantee: an index isn't ready until it is complete, a document has one ready
index per embedding model and chunker, foreign keys hold, an index a crash left
half-built is recognised, and a conversation belongs to its document.

Every test opens its own database under `tmp_path`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from desktop_pdf_translator.storage import records as records_module
from desktop_pdf_translator.storage.migrations import SchemaTooNewError, migrate
from desktop_pdf_translator.storage.records import RecordsStore

MODEL = "sentence-transformers/test-model"
DIMENSIONS = 384
CHUNKER = "1"
PAPER = "C:/papers/paper.pdf"
ANSWER = {
    "answer": "It measures tensile strength.",
    "pdf_references": [{"page": 2, "chunk_id": "chunk_1"}],
}


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


def _count(records: RecordsStore, table: str) -> int:
    return records._conn().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_a_new_database_starts_at_the_current_schema_with_foreign_keys_on(
    records: RecordsStore,
):
    conn = records._conn()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
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


def test_forgetting_a_document_hands_back_its_indexes(records: RecordsStore):
    doc = _document(records)
    index = _ready_index(records, doc)
    unindexed = _document(records, "doc-b", "C:/papers/scan.pdf")

    assert records.delete_document(doc) == [index]
    assert records.delete_document(unindexed) == []
    assert records.delete_document(doc) is None
    assert records.ready_index(doc, MODEL, CHUNKER) is None
    assert records.document_path(doc) is None


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


def test_one_index_can_be_deleted_and_indexes_listed_by_status(records: RecordsStore):
    doc = _document(records)
    ready = _ready_index(records, doc)
    building = records.begin_index(doc, "an-older-model", DIMENSIONS, CHUNKER)

    assert records.index_ids(status="ready") == {ready}
    assert records.index_ids(status="indexing") == {building.id}
    assert records.delete_index(ready) is True
    assert records.delete_index(ready) is False
    assert records.index_ids() == {building.id}


def test_resetting_the_indexes_spares_one_being_built(records: RecordsStore):
    """A reset deletes what is settled. An index still being built belongs to a
    job in flight, which completes or fails it."""
    doc = _document(records)
    ready = _ready_index(records, doc)
    failed = records.begin_index(doc, "an-older-model", DIMENSIONS, CHUNKER)
    records.fail_index(failed.id, "boom")
    building = records.begin_index(
        _document(records, "doc-b", "C:/papers/b.pdf"), MODEL, DIMENSIONS, CHUNKER
    )
    records.add_exchange(doc, "What is measured?", ANSWER)

    assert sorted(records.delete_settled_indexes()) == sorted([ready, failed.id])

    assert records.index_ids() == {building.id}
    assert len(records.messages(doc)) == 2


def test_records_from_a_newer_build_are_refused(tmp_path: Path):
    db = tmp_path / "pdfusion.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 9")
    conn.commit()
    conn.close()

    with pytest.raises(SchemaTooNewError):
        RecordsStore(db).index_ids()


# ---------------------------------------------------------------------------
# chat history (#31)
# ---------------------------------------------------------------------------


def test_a_version_1_database_gains_chat_history_and_keeps_its_records(tmp_path: Path):
    db = tmp_path / "pdfusion.db"
    migrate(db, records_module._MIGRATIONS[:1])
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO documents (id, display_name, size_bytes, created_at, last_opened_at) "
        "VALUES ('doc-a', 'paper.pdf', 1234, 0, 0)"
    )
    conn.execute(
        "INSERT INTO rag_indexes (id, document_id, embedding_model, embedding_dim, "
        "chunker_version, status, chunk_count, created_at) "
        "VALUES ('index-a', 'doc-a', ?, ?, ?, 'ready', 3, 0)",
        (MODEL, DIMENSIONS, CHUNKER),
    )
    conn.commit()
    conn.close()

    records = RecordsStore(db)

    assert records.ready_index("doc-a", MODEL, CHUNKER).id == "index-a"
    assert records._conn().execute("PRAGMA user_version").fetchone()[0] == 2
    records.add_exchange("doc-a", "What is measured?", ANSWER)
    assert [m.role for m in records.messages("doc-a")] == ["user", "assistant"]


def test_questions_and_answers_come_back_in_the_order_they_were_asked(
    records: RecordsStore,
):
    doc = _document(records)
    records.add_exchange(doc, "What is measured?", ANSWER)
    records.add_exchange(doc, "At what temperature?", {"answer": "600 C."})

    messages = records.messages(doc)

    assert [(m.role, m.content) for m in messages] == [
        ("user", "What is measured?"),
        ("assistant", "It measures tensile strength."),
        ("user", "At what temperature?"),
        ("assistant", "600 C."),
    ]
    assert messages[0].answer is None
    assert messages[1].answer == ANSWER


def test_a_conversation_belongs_to_its_document(records: RecordsStore):
    first = _document(records, "doc-a", "C:/downloads/paper.pdf")
    second = _document(records, "doc-b", "C:/desktop/paper.pdf")

    records.add_exchange(first, "About the first paper?", {"answer": "Yes."})

    assert len(records.messages(first)) == 2
    assert records.messages(second) == []


def test_an_answer_for_a_document_that_is_gone_is_not_saved(records: RecordsStore):
    """The document was removed while its question was being answered."""
    with pytest.raises(sqlite3.IntegrityError):
        records.add_exchange("no-such-document", "What is measured?", ANSWER)

    assert _count(records, "chat_messages") == 0


def test_a_conversation_outlives_the_index_it_was_answered_from(records: RecordsStore):
    """A new chunker replaces every document's index. Keyed by index, the
    history would have gone with it."""
    doc = _document(records)
    _ready_index(records, doc, chunker="1")
    records.add_exchange(doc, "What is measured?", ANSWER)

    newer = records.begin_index(doc, MODEL, DIMENSIONS, "2")
    records.complete_index(newer.id, chunk_count=5)

    assert len(records.messages(doc)) == 2


def test_forgetting_a_document_forgets_its_conversation(records: RecordsStore):
    doc = _document(records)
    records.add_exchange(doc, "What is measured?", ANSWER)

    records.delete_document(doc)

    assert _count(records, "chat_messages") == 0


def test_clearing_a_conversation_keeps_the_document_and_its_index(records: RecordsStore):
    doc = _document(records)
    index = _ready_index(records, doc)
    records.add_exchange(doc, "What is measured?", ANSWER)

    assert records.clear_messages(doc) == 2

    assert records.messages(doc) == []
    assert records.ready_index(doc, MODEL, CHUNKER).id == index


def test_the_document_list_says_what_is_stored_for_each(records: RecordsStore):
    older = _document(records, "doc-old", "C:/papers/old.pdf")
    _ready_index(records, older, model="an-older-model")  # not what a question uses
    newer = _document(records, "doc-new", "C:/downloads/new.pdf")
    _ready_index(records, newer)
    records.add_exchange(newer, "Q1?", {"answer": "A1."})
    records.add_exchange(newer, "Q2?", {"answer": "A2."})
    records.upsert_document(newer, "renamed.pdf", 1234, "D:/backup/new.pdf")

    listed = records.list_documents(MODEL, CHUNKER)

    assert [d.id for d in listed] == ["doc-new", "doc-old"]
    new, old = listed
    assert (new.display_name, new.path, new.chunk_count, new.question_count) == (
        "renamed.pdf",
        "D:/backup/new.pdf",
        3,
        2,
    )
    assert (old.path, old.chunk_count, old.question_count) == ("C:/papers/old.pdf", None, 0)
