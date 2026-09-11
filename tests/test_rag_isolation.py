"""Chat answers from the open PDF, and from no other (#59).

It didn't, in two ways this suite pins down:

* the index was keyed by file name, so a second, different `paper.pdf` was
  "Already indexed" and every answer about it came from the first;
* `hybrid_search`'s fallback dropped the document filter, so any failure in the
  keyword pass searched every PDF ever indexed.

(The third — a question with no `document_id` searched everything — is refused
by `AskRequest` now; see `test_rag_api.py`.)

Everything runs against a real ChromaDB under `tmp_path`, with a deterministic
embedding function standing in for the ONNX model: no ~470 MB download, and
nothing reaches the user's AppData.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest
from chromadb import EmbeddingFunction

from desktop_pdf_translator.api.jobs import Job
from desktop_pdf_translator.api.routes import rag as rag_routes
from desktop_pdf_translator.api.schemas import IndexRequest
from desktop_pdf_translator.processors.pdf_cache import compute_file_hash
from desktop_pdf_translator.rag import rag_chain as rag_chain_module
from desktop_pdf_translator.rag.rag_chain import EnhancedRAGChain
from desktop_pdf_translator.rag.vector_store import ChromaDBManager

from conftest import MINIMAL_PDF

QUESTION = "What does the experiment measure?"
ALPHA = "The experiment measures the thermal conductivity of alpha samples."
BETA = "The experiment measures the tensile strength of beta samples."

_DIMENSIONS = 64
_WORD = re.compile(r"\w+")


class HashingEmbeddingFunction(EmbeddingFunction):
    """Bag of words, hashed into a fixed number of buckets.

    Deterministic, and texts that share words land near each other — all that
    retrieval needs to rank these tests' chunks. Its surface mirrors
    `OnnxEmbeddingFunction`, the shape chromadb is known to accept here.
    """

    def __init__(self) -> None:
        pass

    @staticmethod
    def name() -> str:
        return "pdfusion-test-hashing"

    def default_space(self) -> str:
        return "cosine"

    def get_config(self) -> Dict[str, Any]:
        return {}

    @staticmethod
    def build_from_config(config: Dict[str, Any]) -> "HashingEmbeddingFunction":
        return HashingEmbeddingFunction()

    def __call__(self, input: List[str]) -> List[List[float]]:
        vectors = []
        for text in input:
            vector = [0.0] * _DIMENSIONS
            for word in _WORD.findall(text.lower()):
                digest = hashlib.sha256(word.encode("utf-8")).digest()
                vector[int.from_bytes(digest[:4], "little") % _DIMENSIONS] += 1.0
            if not any(vector):
                vector[0] = 1.0  # cosine distance is undefined for a zero vector
            vectors.append(vector)
        return vectors


class _NoKeySettings:
    """No LLM key anywhere: the chain builds no translator, skips HyDE, and
    never reads the developer's own config.toml."""

    class translation:
        preferred_service = None

    @staticmethod
    def has_api_key(service) -> bool:
        return False


class _FakeProcessor:
    """Stands in for `ScientificPDFProcessor` (fitz + camelot + pdfplumber):
    one chunk, whose text says which of the two papers it was read from."""

    def process_pdf(self, path: Path) -> List[Dict[str, Any]]:
        return _chunks(ALPHA if b"alpha" in path.read_bytes() else BETA)


def _chunks(*texts: str) -> List[Dict[str, Any]]:
    return [{"text": text, "page": 0, "metadata": {}} for text in texts]


@pytest.fixture
def store(tmp_path: Path) -> ChromaDBManager:
    return ChromaDBManager(
        persist_directory=tmp_path / "chroma",
        embedding_function=HashingEmbeddingFunction(),
    )


@pytest.fixture
def two_documents(store: ChromaDBManager) -> None:
    async def seed() -> None:
        await store.add_document_chunks(_chunks(ALPHA), "doc-alpha", "C:/a/paper.pdf")
        await store.add_document_chunks(_chunks(BETA), "doc-beta", "C:/b/paper.pdf")

    asyncio.run(seed())


@pytest.fixture
def two_papers(tmp_path: Path) -> tuple[Path, Path]:
    """Two different PDFs sharing a file name — the pair a stem-keyed index
    could not tell apart."""
    first = tmp_path / "downloads" / "paper.pdf"
    second = tmp_path / "desktop" / "paper.pdf"
    for path, marker in ((first, b"alpha"), (second, b"beta")):
        path.parent.mkdir(parents=True)
        path.write_bytes(MINIMAL_PDF + b"% " + marker + b"\n")
    return first, second


@pytest.fixture
def run_index(store: ChromaDBManager, monkeypatch: pytest.MonkeyPatch):
    """Run the real `_run_index` job against the tmp store; return its `done`
    payload."""

    async def get_store() -> ChromaDBManager:
        return store

    monkeypatch.setattr(rag_routes, "_get_store", get_store)
    monkeypatch.setattr(rag_routes, "_load_document_processor", lambda: _FakeProcessor)

    def run(path: Path) -> Dict[str, Any]:
        job = Job(job_id="index")
        asyncio.run(rag_routes._run_index(job, IndexRequest(file_path=str(path))))
        last = job.history[-1]
        assert last["type"] == "done", last
        return last["data"]

    return run


# ---------------------------------------------------------------------------
# document identity
# ---------------------------------------------------------------------------


def test_a_second_pdf_with_the_same_name_gets_its_own_index(
    run_index, two_papers, store: ChromaDBManager
):
    first, second = two_papers

    indexed_first = run_index(first)
    indexed_second = run_index(second)

    assert indexed_first["document_id"] == compute_file_hash(first)
    assert indexed_second["document_id"] == compute_file_hash(second)
    assert indexed_second["document_id"] != indexed_first["document_id"]
    # Keyed by stem, this one was "Already indexed" — with the first file's
    # chunks standing in for its own.
    assert indexed_second["cached"] is False
    chunks = asyncio.run(store.search_by_document(indexed_second["document_id"]))
    assert [c["text"] for c in chunks] == [BETA]


def test_reopening_the_same_pdf_reuses_its_index(run_index, two_papers):
    first, _ = two_papers

    run_index(first)
    again = run_index(first)

    assert again == {"document_id": compute_file_hash(first), "chunks": 1, "cached": True}


# ---------------------------------------------------------------------------
# retrieval stays inside one document
# ---------------------------------------------------------------------------


def test_retrieval_returns_only_the_named_documents_chunks(
    store: ChromaDBManager, two_documents, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(rag_chain_module, "get_settings", lambda: _NoKeySettings())
    chain = EnhancedRAGChain(store)

    results = asyncio.run(chain._retrieve_pdf_knowledge(QUESTION, "doc-beta", 5))

    assert results, "the named document's own chunk should be found"
    assert {r["metadata"]["document_id"] for r in results} == {"doc-beta"}


def test_the_semantic_fallback_keeps_the_document_filter(
    store: ChromaDBManager, two_documents, monkeypatch: pytest.MonkeyPatch
):
    """`hybrid_search` falls back to a plain similarity search when its keyword
    pass fails. The fallback used to drop `filter_metadata`, so this returned
    the other document's chunk as well."""

    def keyword_pass_fails(*args, **kwargs):
        raise RuntimeError("keyword pass failed")

    monkeypatch.setattr(store.collection, "get", keyword_pass_fails)

    results = asyncio.run(
        store.hybrid_search(QUESTION, n_results=4, filter_metadata={"document_id": "doc-beta"})
    )

    assert results
    assert {r["metadata"]["document_id"] for r in results} == {"doc-beta"}


# ---------------------------------------------------------------------------
# deletion
# ---------------------------------------------------------------------------


def test_deleting_reports_how_many_chunks_went(store: ChromaDBManager):
    async def scenario():
        await store.add_document_chunks(_chunks(ALPHA, BETA), "doc", "C:/paper.pdf")
        unknown = await store.delete_document("no-such-document")
        removed = await store.delete_document("doc")
        return unknown, removed, await store.has_document("doc")

    assert asyncio.run(scenario()) == (0, 2, False)
