"""The `/rag` HTTP contract (#59, #31).

Only the rag router is mounted, so no lifespan runs, and the records and the
vector store are stubs: nothing here imports chromadb or loads a model. What the
real stores do with a document is `test_rag_isolation.py`'s job.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional, Sequence

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktop_pdf_translator.api import auth, server
from desktop_pdf_translator.api.jobs import get_registry
from desktop_pdf_translator.api.routes import rag as rag_routes
from desktop_pdf_translator.config import LanguageCode
from desktop_pdf_translator.rag.index_spec import CHUNKER_VERSION, EMBEDDING_MODEL
from desktop_pdf_translator.storage.records import (
    ChatMessageRecord,
    DocumentSummary,
    IndexRecord,
)

TOKEN = "test-token-for-rag-api"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "_TOKEN", TOKEN)


@pytest.fixture(autouse=True)
def _no_real_jobs(monkeypatch: pytest.MonkeyPatch):
    async def _noop(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(rag_routes, "_run_ask", _noop)
    monkeypatch.setattr(rag_routes, "_run_index", _noop)
    monkeypatch.setattr(rag_routes, "_document_locks", {})


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(rag_routes.router)
    return TestClient(app)


def _ready_index(document_id: str = "abc123") -> IndexRecord:
    return IndexRecord(
        id="index-1",
        document_id=document_id,
        embedding_model=EMBEDDING_MODEL,
        embedding_dim=384,
        chunker_version=CHUNKER_VERSION,
        status="ready",
        chunk_count=3,
        error=None,
        created_at=0,
        completed_at=1,
    )


class _StubRecords:
    def __init__(
        self,
        ready: Optional[IndexRecord] = None,
        removed: Optional[Sequence[str]] = None,
        error: Optional[Exception] = None,
        documents: Sequence[DocumentSummary] = (),
        messages: Sequence[ChatMessageRecord] = (),
        settled: Sequence[str] = (),
        index_ids: Sequence[str] = (),
    ):
        self.ready = ready
        # `None`: the document isn't recorded. A sequence: it was, with these indexes.
        self.removed = removed
        self.error = error
        self.documents = list(documents)
        self.stored_messages = list(messages)
        self.settled = list(settled)
        self.ids = set(index_ids)
        self.cleared: List[str] = []

    def ready_index(self, document_id, embedding_model, chunker_version):
        return self.ready

    def document_path(self, document_id):
        return "C:/papers/paper.pdf"

    def delete_document(self, document_id) -> Optional[List[str]]:
        if self.error is not None:
            raise self.error
        return None if self.removed is None else list(self.removed)

    def list_documents(self, embedding_model, chunker_version):
        return self.documents

    def messages(self, document_id):
        return self.stored_messages

    def clear_messages(self, document_id) -> int:
        self.cleared.append(document_id)
        return 0

    def delete_settled_indexes(self) -> List[str]:
        if self.error is not None:
            raise self.error
        return self.settled

    def index_ids(self, status=None):
        return self.ids


class _StubStore:
    def __init__(self, collections: Sequence[str] = (), fail: bool = False) -> None:
        self.dropped: List[str] = []
        self.collections = set(collections)
        self.fail = fail

    def drop_index(self, index_id: str) -> bool:
        if self.fail:
            raise RuntimeError("The process cannot access the file")
        self.dropped.append(index_id)
        return True

    def index_ids(self):
        return self.collections - set(self.dropped)


def _use(
    monkeypatch: pytest.MonkeyPatch, records: _StubRecords, *, loaded: bool = True,
    store: Optional[_StubStore] = None,
) -> _StubStore:
    """Point the routes at stubs. `loaded` says whether this process has opened
    the vector store; nothing here may open it."""
    store = store or _StubStore()

    async def get_store() -> _StubStore:
        pytest.fail("the vector store must not be opened")

    monkeypatch.setattr(rag_routes, "get_records_store", lambda: records)
    monkeypatch.setattr(rag_routes, "_get_store", get_store)
    monkeypatch.setattr(rag_routes, "_loaded_store", lambda: store if loaded else None)
    return store


# ---------------------------------------------------------------------------
# POST /rag/ask
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"question": "What is the ablation?"},
        {"question": "What is the ablation?", "document_id": None},
        {"question": "What is the ablation?", "document_id": ""},
    ],
)
def test_a_question_has_to_name_its_document(client: TestClient, body):
    """A missing `document_id` used to mean "every indexed document" — the
    mode that let chat answer from PDFs other than the open one."""
    response = client.post("/rag/ask", json=body, headers=AUTH)

    assert response.status_code == 422


def test_a_question_about_an_indexed_document_is_accepted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _use(monkeypatch, _StubRecords(ready=_ready_index()))

    response = client.post(
        "/rag/ask",
        json={"question": "What is the ablation?", "document_id": "abc123"},
        headers=AUTH,
    )

    assert response.status_code == 202
    assert response.json()["job_id"]


def test_a_question_about_an_unindexed_document_is_refused_before_a_job_exists(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Searching a document with no chunks would answer "I could not find
    relevant information", as if the document had been read."""
    _use(monkeypatch, _StubRecords(ready=None))
    jobs_before = len(get_registry()._jobs)

    response = client.post(
        "/rag/ask",
        json={"question": "What is the ablation?", "document_id": "abc123"},
        headers=AUTH,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == rag_routes.NOT_INDEXED_MESSAGE
    assert len(get_registry()._jobs) == jobs_before


def _record_asks(monkeypatch: pytest.MonkeyPatch) -> List[tuple]:
    """Replace `_run_ask` with one that records what the route handed it."""
    calls: List[tuple] = []

    async def finished() -> None:
        return None

    def record(*args):
        calls.append(args)
        return finished()

    monkeypatch.setattr(rag_routes, "_run_ask", record)
    return calls


def test_a_question_is_answered_in_the_language_it_names(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _use(monkeypatch, _StubRecords(ready=_ready_index()))
    calls = _record_asks(monkeypatch)

    response = client.post(
        "/rag/ask",
        json={"question": "What is the ablation?", "document_id": "abc123", "target_lang": "ja"},
        headers=AUTH,
    )

    assert response.status_code == 202
    (job, payload, index, document_path, answer_lang), = calls
    assert answer_lang == "ja"


def test_a_question_naming_no_language_is_answered_in_the_configured_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Answers used to be Vietnamese whatever the toolbar said (#31)."""
    _use(monkeypatch, _StubRecords(ready=_ready_index()))
    calls = _record_asks(monkeypatch)
    settings = SimpleNamespace(
        translation=SimpleNamespace(
            default_source_lang=LanguageCode.AUTO,
            default_target_lang=LanguageCode.ENGLISH,
        )
    )
    monkeypatch.setattr(rag_routes, "get_settings", lambda: settings)

    response = client.post(
        "/rag/ask",
        json={"question": "What is the ablation?", "document_id": "abc123"},
        headers=AUTH,
    )

    assert response.status_code == 202
    assert calls[0][-1] == "en"


def test_a_language_the_app_does_not_know_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _use(monkeypatch, _StubRecords(ready=_ready_index()))

    response = client.post(
        "/rag/ask",
        json={"question": "What is the ablation?", "document_id": "abc123", "target_lang": "xx"},
        headers=AUTH,
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /rag/documents, and a document's messages
# ---------------------------------------------------------------------------


def test_the_document_list_is_read_from_the_records_alone(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Opening Settings → Chat must not load chromadb and the embedding model."""
    records = _StubRecords(
        documents=[
            DocumentSummary(
                id="abc123",
                display_name="paper.pdf",
                size_bytes=2048,
                page_count=3,
                last_opened_at=0,
                path="C:/papers/paper.pdf",
                chunk_count=12,
                question_count=2,
            )
        ]
    )
    _use(monkeypatch, records, loaded=False)

    response = client.get("/rag/documents", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "documents": [
            {
                "document_id": "abc123",
                "display_name": "paper.pdf",
                "path": "C:/papers/paper.pdf",
                "size_bytes": 2048,
                "page_count": 3,
                "chunk_count": 12,
                "question_count": 2,
                "last_opened_at": "1970-01-01T00:00:00+00:00",
            }
        ]
    }


def test_a_documents_conversation_comes_back_with_its_answers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    answer = {
        "answer": "Tensile strength.",
        "pdf_references": [
            {
                "type": "pdf",
                "page": 2,
                "text": "The tensile strength of copper…",
                "confidence": 0.5,
                "document_id": "abc123",
                "document_path": "C:/papers/paper.pdf",
                "chunk_id": "chunk_1",
            }
        ],
    }
    records = _StubRecords(
        messages=[
            ChatMessageRecord(1, "abc123", "user", "What is measured?", None, 0),
            ChatMessageRecord(2, "abc123", "assistant", "Tensile strength.", answer, 0),
        ]
    )
    _use(monkeypatch, records, loaded=False)

    response = client.get("/rag/document/abc123/messages", headers=AUTH)

    assert response.status_code == 200
    messages = response.json()["messages"]
    assert [(m["role"], m["text"]) for m in messages] == [
        ("user", "What is measured?"),
        ("assistant", "Tensile strength."),
    ]
    assert messages[0]["answer"] is None
    assert messages[1]["answer"]["pdf_references"][0]["page"] == 2


def test_clearing_a_conversation(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    records = _StubRecords()
    _use(monkeypatch, records, loaded=False)

    response = client.delete("/rag/document/abc123/messages", headers=AUTH)

    assert response.status_code == 204
    assert records.cleared == ["abc123"]


# ---------------------------------------------------------------------------
# DELETE /rag/document/{document_id}
# ---------------------------------------------------------------------------


def test_removing_a_document_drops_every_one_of_its_collections(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    store = _use(monkeypatch, _StubRecords(removed=("index-1", "index-0")))

    response = client.delete("/rag/document/abc123", headers=AUTH)

    assert response.status_code == 204
    assert store.dropped == ["index-1", "index-0"]


def test_removing_a_document_never_opens_the_vector_store(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """`_recover` drops the collections when the store opens."""
    store = _use(monkeypatch, _StubRecords(removed=("index-1",)), loaded=False)

    response = client.delete("/rag/document/abc123", headers=AUTH)

    assert response.status_code == 204
    assert store.dropped == []


@pytest.mark.parametrize(
    "records, expected",
    [
        (_StubRecords(removed=()), 204),
        # These came back the other way round before #59: 204 for a document
        # with nothing to delete, 404 "Document not found" for a storage failure.
        (_StubRecords(removed=None), 404),
        (_StubRecords(error=RuntimeError("disk I/O error")), 500),
    ],
    ids=["recorded-without-an-index", "unknown", "storage-failure"],
)
def test_removing_says_what_happened(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, records: _StubRecords, expected: int
):
    store = _use(monkeypatch, records)

    response = client.delete("/rag/document/abc123", headers=AUTH)

    assert response.status_code == expected
    assert store.dropped == []


# ---------------------------------------------------------------------------
# POST /rag/reset
# ---------------------------------------------------------------------------


def test_reset_drops_the_collections_through_the_open_store(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """The deleted indexes' collections go, and so does one no row accounts for.
    One being built stays: its row is still there."""
    records = _StubRecords(settled=["index-1"], index_ids=["index-building"])
    store = _use(
        monkeypatch, records,
        store=_StubStore(collections=["index-1", "index-building", "orphan"]),
    )

    response = client.post("/rag/reset", headers=AUTH)

    assert (response.status_code, response.json()) == (200, {"removed": 1})
    assert store.dropped == ["index-1", "orphan"]


def test_reset_deletes_a_vector_store_this_process_has_not_opened(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """The way out of a store too damaged to open: no client holds its files,
    so the directory itself goes."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    vectors = tmp_path / "PDFusion" / "vectors"
    (vectors / "segment").mkdir(parents=True)
    (vectors / "chroma.sqlite3").write_bytes(b"SQLite format 3\x00")
    store = _use(monkeypatch, _StubRecords(settled=["index-1", "index-2"]), loaded=False)

    response = client.post("/rag/reset", headers=AUTH)

    assert (response.status_code, response.json()) == (200, {"removed": 2})
    assert not vectors.exists()
    assert store.dropped == []


def test_reset_that_cannot_delete_the_files_says_to_restart(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _use(monkeypatch, _StubRecords(settled=["index-1"]), store=_StubStore(fail=True))

    response = client.post("/rag/reset", headers=AUTH)

    assert response.status_code == 500
    assert response.json()["detail"] == rag_routes.RESET_FILES_FAILED_MESSAGE


# ---------------------------------------------------------------------------
# startup
# ---------------------------------------------------------------------------


def test_the_legacy_vector_stores_are_removed_and_the_current_one_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = tmp_path / "PDFusion"
    for name in ("chroma_db_v2", "chroma_db", "vectors"):
        (root / name).mkdir(parents=True)
        (root / name / "chroma.sqlite3").write_bytes(b"SQLite format 3\x00")

    server._remove_legacy_vector_stores()

    assert sorted(p.name for p in root.iterdir()) == ["vectors"]
