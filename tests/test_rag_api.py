"""The `/rag` HTTP contract (#59).

Only the rag router is mounted, so no lifespan runs, and the store and chain are
stubs: nothing here imports chromadb or loads an embedding model. What the store
itself does with a document id is `test_rag_isolation.py`'s job.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktop_pdf_translator.api import auth
from desktop_pdf_translator.api.jobs import Job
from desktop_pdf_translator.api.routes import rag as rag_routes
from desktop_pdf_translator.api.schemas import AskRequest

TOKEN = "test-token-for-rag-api"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# Captured before `_no_real_jobs` replaces it, for the test that runs it.
_REAL_RUN_ASK = rag_routes._run_ask


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "_TOKEN", TOKEN)


@pytest.fixture(autouse=True)
def _no_real_jobs(monkeypatch: pytest.MonkeyPatch):
    async def _noop(job, payload) -> None:
        return None

    monkeypatch.setattr(rag_routes, "_run_ask", _noop)
    monkeypatch.setattr(rag_routes, "_run_index", _noop)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(rag_routes.router)
    return TestClient(app)


class _StubStore:
    def __init__(self, removed: int = 0, error: Optional[Exception] = None):
        self.removed = removed
        self.error = error

    async def delete_document(self, document_id: str) -> int:
        if self.error is not None:
            raise self.error
        return self.removed


def _use_store(monkeypatch: pytest.MonkeyPatch, store: _StubStore) -> None:
    async def get_store() -> _StubStore:
        return store

    monkeypatch.setattr(rag_routes, "_get_store", get_store)


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


def test_a_question_about_one_document_is_accepted(client: TestClient):
    response = client.post(
        "/rag/ask",
        json={"question": "What is the ablation?", "document_id": "abc123"},
        headers=AUTH,
    )

    assert response.status_code == 202
    assert response.json()["job_id"]


def test_a_question_about_an_unindexed_document_ends_in_an_error(
    monkeypatch: pytest.MonkeyPatch,
):
    """Searching a document with no chunks would answer "I could not find
    relevant information", as if the document had been read."""

    class _EmptyStore:
        async def has_document(self, document_id: str) -> bool:
            return False

    class _Chain:
        vector_store = _EmptyStore()

        async def answer_question(self, **kwargs):
            raise AssertionError("an unindexed document must not be searched")

    async def get_chain() -> _Chain:
        return _Chain()

    monkeypatch.setattr(rag_routes, "_get_chain", get_chain)
    job = Job(job_id="ask")

    asyncio.run(_REAL_RUN_ASK(job, AskRequest(question="hi", document_id="abc123")))

    assert [(e["type"], e["data"]) for e in job.history] == [
        ("error", {"message": rag_routes.NOT_INDEXED_MESSAGE})
    ]


# ---------------------------------------------------------------------------
# DELETE /rag/document/{document_id}
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "store, expected",
    [
        (_StubStore(removed=3), 204),
        # Both of these used to come back the other way round: 204 for an id
        # that matched nothing, 404 "Document not found" for a storage failure.
        (_StubStore(removed=0), 404),
        (_StubStore(error=RuntimeError("disk I/O error")), 500),
    ],
    ids=["deleted", "unknown", "storage-failure"],
)
def test_deleting_a_document_says_what_happened(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, store: _StubStore, expected: int
):
    _use_store(monkeypatch, store)

    response = client.delete("/rag/document/abc123", headers=AUTH)

    assert response.status_code == expected
