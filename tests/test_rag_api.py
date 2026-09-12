"""The `/rag` HTTP contract (#59).

Only the rag router is mounted, so no lifespan runs, and the records and the
vector store are stubs: nothing here imports chromadb or loads a model. What the
real stores do with a document is `test_rag_isolation.py`'s job.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktop_pdf_translator.api import auth, server
from desktop_pdf_translator.api.jobs import get_registry
from desktop_pdf_translator.api.routes import rag as rag_routes
from desktop_pdf_translator.config import LanguageCode
from desktop_pdf_translator.rag.index_spec import CHUNKER_VERSION, EMBEDDING_MODEL
from desktop_pdf_translator.storage.records import IndexRecord

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
        removed: tuple = (),
        error: Optional[Exception] = None,
    ):
        self.ready = ready
        self.removed = list(removed)
        self.error = error

    def ready_index(self, document_id, embedding_model, chunker_version):
        return self.ready

    def document_path(self, document_id):
        return "C:/papers/paper.pdf"

    def delete_document_indexes(self, document_id) -> List[str]:
        if self.error is not None:
            raise self.error
        return self.removed


class _StubStore:
    def __init__(self) -> None:
        self.dropped: List[str] = []

    def drop_index(self, index_id: str) -> bool:
        self.dropped.append(index_id)
        return True


def _use(monkeypatch: pytest.MonkeyPatch, records: _StubRecords) -> _StubStore:
    store = _StubStore()

    async def get_store() -> _StubStore:
        return store

    monkeypatch.setattr(rag_routes, "get_records_store", lambda: records)
    monkeypatch.setattr(rag_routes, "_get_store", get_store)
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
# DELETE /rag/document/{document_id}
# ---------------------------------------------------------------------------


def test_deleting_a_document_drops_every_one_of_its_collections(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    store = _use(monkeypatch, _StubRecords(removed=("index-1", "index-0")))

    response = client.delete("/rag/document/abc123", headers=AUTH)

    assert response.status_code == 204
    assert store.dropped == ["index-1", "index-0"]


@pytest.mark.parametrize(
    "records, expected",
    [
        # These came back the other way round before #59: 204 for a document
        # with nothing to delete, 404 "Document not found" for a storage failure.
        (_StubRecords(removed=()), 404),
        (_StubRecords(error=RuntimeError("disk I/O error")), 500),
    ],
    ids=["unknown", "storage-failure"],
)
def test_deleting_says_what_went_wrong(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, records: _StubRecords, expected: int
):
    store = _use(monkeypatch, records)

    response = client.delete("/rag/document/abc123", headers=AUTH)

    assert response.status_code == expected
    assert store.dropped == []


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
