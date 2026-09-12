"""Chat answers from the open PDF, and from no other (#59).

A document is the SHA-256 of its bytes, recorded in `pdfusion.db`, and each of
its chat indexes is a row there whose chunks live in a ChromaDB collection of
their own. A question names one index, so another document's chunks are never
within reach.

Everything here is real — the `_run_index` job, the records database, ChromaDB,
the chain's retrieval — under `tmp_path`, with a deterministic embedding
function standing in for the ONNX model: nothing downloads, and nothing reaches
the user's AppData.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

import pytest
from chromadb import EmbeddingFunction
from fastapi import HTTPException

from desktop_pdf_translator.api.jobs import Job
from desktop_pdf_translator.api.routes import rag as rag_routes
from desktop_pdf_translator.api.schemas import AskRequest, IndexRequest
from desktop_pdf_translator.config import TranslationService
from desktop_pdf_translator.processors.pdf_cache import compute_file_hash
from desktop_pdf_translator.rag import rag_chain as rag_chain_module
from desktop_pdf_translator.rag.index_spec import (
    CHUNKER_VERSION,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
)
from desktop_pdf_translator.rag.rag_chain import AnswerModel, EnhancedRAGChain
from desktop_pdf_translator.rag.vector_store import ChromaDBManager
from desktop_pdf_translator.storage.records import IndexRecord, RecordsStore

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
    """No LLM key anywhere: the chain builds no translator and skips HyDE."""

    class translation:
        preferred_service = None

    @staticmethod
    def has_api_key(service) -> bool:
        return False


class _FakeProcessor:
    """Stands in for `ScientificPDFProcessor` (fitz + camelot + pdfplumber):
    one chunk on one page, whose text says which paper it was read from."""

    def __init__(self) -> None:
        self.page_layouts = {0: {}}

    def process_pdf(self, path: Path) -> List[Dict[str, Any]]:
        return _chunks(ALPHA if b"alpha" in path.read_bytes() else BETA)


def _chunks(*texts: str) -> List[Dict[str, Any]]:
    return [{"text": text, "page": 0, "metadata": {}} for text in texts]


def _write_paper(path: Path, marker: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(MINIMAL_PDF + b"% " + marker + b"\n")
    return path


def run_index(path: Path) -> Dict[str, Any]:
    """Run the real `_run_index` job; return its terminal event."""
    job = Job(job_id="index")
    asyncio.run(rag_routes._run_index(job, IndexRequest(file_path=str(path))))
    return job.history[-1]


@pytest.fixture
def store(tmp_path: Path) -> ChromaDBManager:
    return ChromaDBManager(
        persist_directory=tmp_path / "vectors",
        embedding_function=HashingEmbeddingFunction(),
    )


@pytest.fixture
def records(tmp_path: Path) -> RecordsStore:
    return RecordsStore(tmp_path / "pdfusion.db")


@pytest.fixture
def sidecar(store: ChromaDBManager, records: RecordsStore, monkeypatch: pytest.MonkeyPatch):
    """Point the rag routes at the tmp stores."""

    async def get_store() -> ChromaDBManager:
        return store

    monkeypatch.setattr(rag_routes, "_get_store", get_store)
    monkeypatch.setattr(rag_routes, "_loaded_store", lambda: store)
    monkeypatch.setattr(rag_routes, "get_records_store", lambda: records)
    monkeypatch.setattr(rag_routes, "_load_document_processor", lambda: _FakeProcessor)
    monkeypatch.setattr(rag_routes, "_document_locks", {})


@pytest.fixture
def two_papers(tmp_path: Path) -> Tuple[Path, Path]:
    """Two different PDFs sharing a file name — the pair a name-keyed index
    could not tell apart."""
    return (
        _write_paper(tmp_path / "downloads" / "paper.pdf", b"alpha"),
        _write_paper(tmp_path / "desktop" / "paper.pdf", b"beta"),
    )


@pytest.fixture
def chain(store: ChromaDBManager, monkeypatch: pytest.MonkeyPatch) -> EnhancedRAGChain:
    monkeypatch.setattr(rag_chain_module, "get_settings", lambda: _NoKeySettings())
    return EnhancedRAGChain(store)


def _ready(records: RecordsStore, done: Dict[str, Any]) -> IndexRecord:
    index = records.ready_index(done["data"]["document_id"], EMBEDDING_MODEL, CHUNKER_VERSION)
    assert index is not None
    return index


# ---------------------------------------------------------------------------
# document identity
# ---------------------------------------------------------------------------


def test_a_second_pdf_with_the_same_name_gets_its_own_index(
    sidecar, two_papers, store: ChromaDBManager, records: RecordsStore
):
    first, second = two_papers

    indexed_first = run_index(first)
    indexed_second = run_index(second)

    assert indexed_first["type"] == indexed_second["type"] == "done"
    assert indexed_first["data"]["document_id"] == compute_file_hash(first)
    assert indexed_second["data"]["document_id"] == compute_file_hash(second)
    # Keyed by file name, this one was "Already indexed", with the first
    # paper's chunks standing in for its own.
    assert indexed_second["data"]["cached"] is False
    index_first, index_second = _ready(records, indexed_first), _ready(records, indexed_second)
    assert [c["text"] for c in asyncio.run(store.get_chunks(index_second.id))] == [BETA]
    assert store.index_ids() == {index_first.id, index_second.id}


def test_reopening_the_same_pdf_reuses_its_index(
    sidecar, two_papers, store: ChromaDBManager, records: RecordsStore
):
    first, _ = two_papers

    run_index(first)
    again = run_index(first)

    assert again["data"] == {"document_id": compute_file_hash(first), "chunks": 1, "cached": True}
    assert len(records.index_ids()) == len(store.index_ids()) == 1


def test_new_bytes_at_the_same_path_are_a_new_document(
    sidecar, tmp_path: Path, records: RecordsStore
):
    paper = _write_paper(tmp_path / "paper.pdf", b"alpha")
    before = run_index(paper)["data"]["document_id"]

    _write_paper(paper, b"beta")
    after = run_index(paper)

    assert after["data"]["document_id"] != before
    assert after["data"]["cached"] is False
    assert records.document_path(after["data"]["document_id"]) == str(paper)


# ---------------------------------------------------------------------------
# an index is ready only when it is whole
# ---------------------------------------------------------------------------


def test_a_pdf_with_no_text_ends_in_an_error_and_leaves_no_index(
    sidecar, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    store: ChromaDBManager, records: RecordsStore,
):
    class _Scanned(_FakeProcessor):
        def process_pdf(self, path: Path) -> List[Dict[str, Any]]:
            return []

    monkeypatch.setattr(rag_routes, "_load_document_processor", lambda: _Scanned)

    result = run_index(_write_paper(tmp_path / "scan.pdf", b"scan"))

    assert (result["type"], result["data"]) == ("error", {"message": rag_routes.NO_TEXT_MESSAGE})
    assert records.index_ids() == store.index_ids() == set()


def test_an_index_that_fails_partway_is_failed_and_dropped(
    sidecar, two_papers, monkeypatch: pytest.MonkeyPatch,
    store: ChromaDBManager, records: RecordsStore,
):
    add_chunks = store.add_chunks

    async def add_then_fail(index_id: str, chunks):
        await add_chunks(index_id, chunks)
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "add_chunks", add_then_fail)

    result = run_index(two_papers[0])

    assert result["type"] == "error"
    (index_id,) = records.index_ids()
    assert records.get_index(index_id).status == "failed"
    assert store.index_ids() == set()


def test_an_index_a_crash_left_half_built_is_failed_and_dropped_on_recovery(
    store: ChromaDBManager, records: RecordsStore, tmp_path: Path
):
    paper = _write_paper(tmp_path / "paper.pdf", b"alpha")
    document_id = compute_file_hash(paper)
    records.upsert_document(document_id, paper.name, paper.stat().st_size, str(paper))
    interrupted = records.begin_index(
        document_id, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS, CHUNKER_VERSION
    )
    asyncio.run(store.add_chunks(interrupted.id, _chunks(ALPHA)))

    rag_routes._recover(records, store)

    assert records.get_index(interrupted.id).status == "failed"
    assert records.ready_index(document_id, EMBEDDING_MODEL, CHUNKER_VERSION) is None
    assert store.index_ids() == set()


def test_a_collection_no_record_accounts_for_is_dropped_on_recovery(
    store: ChromaDBManager, records: RecordsStore
):
    asyncio.run(store.add_chunks("orphan", _chunks(ALPHA)))

    rag_routes._recover(records, store)

    assert store.index_ids() == set()


# ---------------------------------------------------------------------------
# a question stays inside one index
# ---------------------------------------------------------------------------


def test_retrieval_reaches_only_the_named_index(
    sidecar, two_papers, records: RecordsStore, chain: EnhancedRAGChain
):
    _, second = two_papers
    run_index(two_papers[0])
    index_second = _ready(records, run_index(second))

    results = asyncio.run(chain._retrieve_pdf_knowledge(QUESTION, index_second.id, 5))

    assert results, "the named document's own chunk should be found"
    assert {r.get("original_text", r["text"]) for r in results} == {BETA}


def test_a_question_reads_its_index_once_and_cites_its_document(
    sidecar, two_papers, records: RecordsStore, store: ChromaDBManager,
    chain: EnhancedRAGChain, monkeypatch: pytest.MonkeyPatch,
):
    """Retrieval used to re-read the document's chunks for each keyword pass
    and for every candidate's surrounding context — up to 12 reads."""
    run_index(two_papers[0])
    index_second = _ready(records, run_index(two_papers[1]))
    reads: List[str] = []
    get_chunks = store.get_chunks

    async def counted(index_id: str):
        reads.append(index_id)
        return await get_chunks(index_id)

    monkeypatch.setattr(store, "get_chunks", counted)

    answer = asyncio.run(
        chain.answer_question(
            question=QUESTION,
            index_id=index_second.id,
            document_id=index_second.document_id,
            document_path="C:/desktop/paper.pdf",
        )
    )

    assert reads == [index_second.id]
    assert answer["pdf_references"]
    assert {(r["document_id"], r["document_path"]) for r in answer["pdf_references"]} == {
        (index_second.document_id, "C:/desktop/paper.pdf")
    }


# ---------------------------------------------------------------------------
# deletion
# ---------------------------------------------------------------------------


def test_removing_a_document_forgets_its_index_and_conversation_and_only_its(
    sidecar, two_papers, records: RecordsStore, store: ChromaDBManager
):
    index_first = _ready(records, run_index(two_papers[0]))
    index_second = _ready(records, run_index(two_papers[1]))
    records.add_exchange(index_first.document_id, QUESTION, {"answer": "Alpha."})
    records.add_exchange(index_second.document_id, QUESTION, {"answer": "Beta."})

    asyncio.run(rag_routes.delete_document(index_first.document_id))

    assert store.index_ids() == {index_second.id}
    assert records.index_ids() == {index_second.id}
    assert records.messages(index_first.document_id) == []
    assert len(records.messages(index_second.document_id)) == 2
    with pytest.raises(HTTPException) as again:
        asyncio.run(rag_routes.delete_document(index_first.document_id))
    assert again.value.status_code == 404


# ---------------------------------------------------------------------------
# answers (#31)
# ---------------------------------------------------------------------------


class _SdkError(Exception):
    """A provider SDK's error, carrying its HTTP status as the SDKs do."""

    def __init__(self, status_code: int):
        super().__init__(f"Error code: {status_code}")
        self.status_code = status_code


class _Translator:
    """An LLM backend's `generate`: records every call, and returns `reply`,
    or raises it."""

    def __init__(self, reply):
        self.reply = reply
        self.calls: List[Tuple[str, str]] = []

    def generate(self, prompt: str, system: str = None, max_tokens: int = 1000):
        self.calls.append((prompt, system))
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


class _KeyedSettings:
    """An OpenAI key, model and endpoint, as a `PUT /config` leaves them."""

    def __init__(self, api_key: str, base_url: str | None = None):
        self.translation = SimpleNamespace(preferred_service=TranslationService.OPENAI)
        self.openai = SimpleNamespace(api_key=api_key, model="gpt-test", base_url=base_url)

    def has_api_key(self, service) -> bool:
        return service == TranslationService.OPENAI


def _answer_with(chain: EnhancedRAGChain, monkeypatch: pytest.MonkeyPatch, translator) -> None:
    monkeypatch.setattr(
        chain, "_answer_model", lambda: AnswerModel(TranslationService.OPENAI, translator)
    )


def _index_of(
    records: RecordsStore, store: ChromaDBManager, tmp_path: Path, *texts: str
) -> IndexRecord:
    """A document with one ready index holding `texts`, a chunk each."""
    paper = _write_paper(tmp_path / "paper.pdf", "".join(texts).encode())
    document_id = compute_file_hash(paper)
    records.upsert_document(document_id, paper.name, paper.stat().st_size, str(paper))
    index = records.begin_index(
        document_id, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS, CHUNKER_VERSION
    )
    asyncio.run(store.add_chunks(index.id, _chunks(*texts)))
    records.complete_index(index.id, len(texts))
    return records.get_index(index.id)


@pytest.fixture
def asking(sidecar, chain: EnhancedRAGChain, monkeypatch: pytest.MonkeyPatch) -> EnhancedRAGChain:
    """`_run_ask` answers with the test chain."""

    async def get_chain() -> EnhancedRAGChain:
        return chain

    monkeypatch.setattr(rag_routes, "_get_chain", get_chain)
    return chain


def run_ask(index: IndexRecord, answer_lang: str = "en") -> Dict[str, Any]:
    """Run the real `_run_ask` job; return its terminal event."""
    job = Job(job_id="ask")
    payload = AskRequest(question=QUESTION, document_id=index.document_id)
    asyncio.run(rag_routes._run_ask(job, payload, index, "C:/papers/paper.pdf", answer_lang))
    return job.history[-1]


def test_the_chunk_with_the_questions_rare_word_comes_first(
    store: ChromaDBManager, records: RecordsStore, chain: EnhancedRAGChain, tmp_path: Path
):
    """Keyword scores are BM25 now: "samples" is in every chunk and says
    little, "graphene" is in one."""
    index = _index_of(
        records, store, tmp_path,
        "The samples were heated slowly.",
        "The graphene samples were heated slowly.",
        "The samples were cooled slowly.",
    )

    results = asyncio.run(
        chain._retrieve_pdf_knowledge("Which samples contain graphene?", index.id, 3)
    )

    assert results[0]["original_text"] == "The graphene samples were heated slowly."


def test_a_rejected_key_ends_in_an_error_rather_than_an_answer(
    asking: EnhancedRAGChain, two_papers, records: RecordsStore,
    monkeypatch: pytest.MonkeyPatch,
):
    """It used to be caught, and the chat showed the template answer, or
    "Sorry, I cannot answer this question due to an error", as the answer."""
    index = _ready(records, run_index(two_papers[0]))
    _answer_with(asking, monkeypatch, _Translator(_SdkError(401)))

    result = run_ask(index)

    assert result["type"] == "error"
    assert result["data"]["message"].startswith("OpenAI rejected the API key")
    assert "code" not in result["data"]
    assert records.index_ids() == {index.id}


def test_with_nothing_retrieved_no_model_is_asked(chain: EnhancedRAGChain):
    """With no context, the model would answer from its own knowledge, as if the
    document had said it."""
    translator = _Translator(AssertionError("the model must not be asked"))

    answer = asyncio.run(
        chain._generate_answer(
            QUESTION, [], AnswerModel(TranslationService.OPENAI, translator), "en"
        )
    )

    assert answer == rag_chain_module.NOTHING_FOUND_ANSWER
    assert translator.calls == []


def test_the_answer_is_asked_for_in_the_requested_language_and_the_search_is_not(
    sidecar, two_papers, records: RecordsStore, chain: EnhancedRAGChain,
    monkeypatch: pytest.MonkeyPatch,
):
    """Answers were Vietnamese whatever was chosen. The HyDE text is a search
    query: in the answer's language, it would miss an English document's words."""
    index = _ready(records, run_index(two_papers[0]))
    translator = _Translator("答え")
    _answer_with(chain, monkeypatch, translator)

    answer = asyncio.run(
        chain.answer_question(
            question=QUESTION,
            index_id=index.id,
            document_id=index.document_id,
            document_path="C:/papers/paper.pdf",
            answer_lang="ja",
        )
    )

    assert answer["answer"] == "答え"
    (hyde_prompt, hyde_system), (prompt, system) = translator.calls
    assert "Japanese" in prompt and "Japanese" in system
    assert "Vietnamese" not in prompt + system
    assert "Answer in" not in hyde_prompt + hyde_system


def test_a_key_saved_after_chat_started_is_used_without_rebuilding_the_chain(
    store: ChromaDBManager, chain: EnhancedRAGChain, monkeypatch: pytest.MonkeyPatch
):
    """`PUT /config` replaces the settings object, and the chain used to keep
    the one it started with. Recovery must not run again to pick up a key: it
    would fail and drop an index being built at that moment."""
    current: Dict[str, Any] = {}
    built: List[str] = []

    def create_translator(service=None, lang_in=None, lang_out=None, **kwargs):
        built.append(current["settings"].openai.api_key)
        return _Translator("answer")

    monkeypatch.setattr(
        rag_chain_module.TranslatorFactory, "create_translator", create_translator
    )
    monkeypatch.setattr(rag_routes, "_rag_chain", chain)
    monkeypatch.setattr(rag_routes, "_vector_store", store)
    monkeypatch.setattr(
        rag_routes, "_recover", lambda *args: pytest.fail("recovery ran again")
    )

    assert chain._answer_model() is None  # no key yet: template answers

    current["settings"] = _KeyedSettings("sk-first")
    monkeypatch.setattr(rag_chain_module, "get_settings", lambda: current["settings"])
    first = chain._answer_model()
    assert asyncio.run(rag_routes._get_chain()) is chain
    assert chain._answer_model() is first

    current["settings"] = _KeyedSettings("sk-second")
    second = chain._answer_model()

    assert (first.service, second.service) == (TranslationService.OPENAI,) * 2
    assert second is not first
    assert built == ["sk-first", "sk-second"]


def test_a_new_endpoint_builds_the_answer_model_again(
    chain: EnhancedRAGChain, monkeypatch: pytest.MonkeyPatch
):
    """Pointing OpenAI at Ollama can keep the service, the key and the model
    name. A model kept from before would go on answering from the old server
    (#32)."""
    current: Dict[str, Any] = {"settings": _KeyedSettings("ollama")}
    built: List[Any] = []

    def create_translator(service=None, lang_in=None, lang_out=None, **kwargs):
        built.append(current["settings"].openai.base_url)
        return _Translator("answer")

    monkeypatch.setattr(
        rag_chain_module.TranslatorFactory, "create_translator", create_translator
    )
    monkeypatch.setattr(rag_chain_module, "get_settings", lambda: current["settings"])

    first = chain._answer_model()
    current["settings"] = _KeyedSettings("ollama", base_url="http://localhost:11434/v1")
    second = chain._answer_model()

    assert second is not first
    assert built == [None, "http://localhost:11434/v1"]


# ---------------------------------------------------------------------------
# a ready index whose chunks are gone (#31)
# ---------------------------------------------------------------------------


def test_recovery_forgets_a_ready_index_whose_collection_is_gone(
    store: ChromaDBManager, records: RecordsStore, tmp_path: Path
):
    """Otherwise `/rag/ask`'s pre-flight accepts the question, and every
    question about the document fails."""
    index = _index_of(records, store, tmp_path, ALPHA)
    store.drop_index(index.id)

    rag_routes._recover(records, store)

    assert records.index_ids() == set()


def test_indexing_builds_again_a_ready_index_whose_collection_is_gone(
    sidecar, two_papers, store: ChromaDBManager, records: RecordsStore
):
    first = _ready(records, run_index(two_papers[0]))
    store.drop_index(first.id)

    again = run_index(two_papers[0])

    assert again["data"]["cached"] is False
    rebuilt = _ready(records, again)
    assert rebuilt.id != first.id
    assert store.index_ids() == records.index_ids() == {rebuilt.id}


def test_a_question_whose_index_is_gone_ends_in_an_error_and_clears_the_index(
    asking: EnhancedRAGChain, two_papers, store: ChromaDBManager, records: RecordsStore
):
    """The error's `code` is what makes the chat panel index the document again."""
    index = _ready(records, run_index(two_papers[0]))
    store.drop_index(index.id)

    result = run_ask(index)

    assert (result["type"], result["data"]) == (
        "error",
        {
            "message": rag_routes.INDEX_UNAVAILABLE_MESSAGE,
            "code": rag_routes.INDEX_UNAVAILABLE,
        },
    )
    assert records.index_ids() == set()


# ---------------------------------------------------------------------------
# chat history and reset (#31)
# ---------------------------------------------------------------------------


def test_an_answer_is_saved_under_the_document_it_was_asked_about(
    asking: EnhancedRAGChain, two_papers, records: RecordsStore
):
    """The question was accepted for the first paper; the second was opened
    before the answer arrived. The panel shows neither in the second's chat."""
    index_first = _ready(records, run_index(two_papers[0]))
    index_second = _ready(records, run_index(two_papers[1]))

    result = run_ask(index_first)

    assert result["type"] == "done"
    user, assistant = records.messages(index_first.document_id)
    assert (user.role, user.content) == ("user", QUESTION)
    assert assistant.answer["answer"] == result["data"]["answer"]
    assert {r["document_id"] for r in assistant.answer["pdf_references"]} == {
        index_first.document_id
    }
    assert records.messages(index_second.document_id) == []


def test_a_question_that_fails_is_not_saved(
    asking: EnhancedRAGChain, two_papers, records: RecordsStore,
    monkeypatch: pytest.MonkeyPatch,
):
    index = _ready(records, run_index(two_papers[0]))
    _answer_with(asking, monkeypatch, _Translator(_SdkError(401)))

    assert run_ask(index)["type"] == "error"

    assert records.messages(index.document_id) == []


def test_indexing_names_the_document_before_the_vector_store_opens(
    sidecar, two_papers, monkeypatch: pytest.MonkeyPatch
):
    """Opening the store loads the embedding model, seconds on first use. The
    chat panel shows the document's saved conversation meanwhile."""
    job = Job(job_id="index")
    events_before_store: List[int] = []
    get_store = rag_routes._get_store

    async def counting_get_store():
        events_before_store.append(len(job.history))
        return await get_store()

    monkeypatch.setattr(rag_routes, "_get_store", counting_get_store)

    asyncio.run(rag_routes._run_index(job, IndexRequest(file_path=str(two_papers[0]))))

    first = job.history[0]
    assert first["type"] == "progress"
    assert first["data"]["document_id"] == compute_file_hash(two_papers[0])
    assert events_before_store == [1]


def test_reset_deletes_every_index_but_one_being_built_and_keeps_conversations(
    sidecar, two_papers, store: ChromaDBManager, records: RecordsStore, tmp_path: Path
):
    index_first = _ready(records, run_index(two_papers[0]))
    _ready(records, run_index(two_papers[1]))
    records.add_exchange(index_first.document_id, QUESTION, {"answer": "Alpha."})
    third = _write_paper(tmp_path / "third.pdf", b"gamma")
    records.upsert_document(
        compute_file_hash(third), third.name, third.stat().st_size, str(third)
    )
    building = records.begin_index(
        compute_file_hash(third), EMBEDDING_MODEL, EMBEDDING_DIMENSIONS, CHUNKER_VERSION
    )
    asyncio.run(store.add_chunks(building.id, _chunks(ALPHA)))

    result = asyncio.run(rag_routes.reset_indexes())

    assert result.removed == 2
    assert records.index_ids() == store.index_ids() == {building.id}
    assert len(records.messages(index_first.document_id)) == 2
