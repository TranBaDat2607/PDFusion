"""RAG endpoints — index a PDF, ask a question, stream events.

The `rag` package loads torch, chromadb, sentence-transformers and camelot
(~10 s). RAG is off by default, so most users never need any of it — every
import of it below lives inside the handler that needs it, and the sidecar
boots without paying for it.

What exists is recorded in `pdfusion.db` (`storage/records.py`): a document is
the SHA-256 of its bytes, and each chat index over it is a row whose chunks live
in a ChromaDB collection of their own. A question names one document and is
answered from that document's ready index, and from nothing else (#59). A
question that can't be answered ends in an `error` event, never in an answer
that reports the failure (#31). A document's chat history is recorded there
too, and `/rag/documents`, Remove and Reset manage what is stored.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sse_starlette.sse import EventSourceResponse

from ...config import get_settings
from ...processors.pdf_cache import compute_file_hash
from ...rag.errors import IndexUnavailableError
from ...rag.index_spec import CHUNKER_VERSION, EMBEDDING_DIMENSIONS, EMBEDDING_MODEL
from ...storage.records import IndexRecord, RecordsStore, get_records_store
from ...storage.sqlite import ms_to_iso
from ...translators.capabilities import resolve_languages
from ...utils.paths import appdata_dir
from ..auth import require_token
from ..jobs import Job, get_registry, serialize_sse_event
from ..schemas import (
    AskRequest,
    ChatHistoryResponse,
    ChatMessageResponse,
    DocumentListResponse,
    DocumentSummaryResponse,
    IndexRequest,
    JobAccepted,
    ResetIndexesResponse,
)

if TYPE_CHECKING:
    from ...rag.rag_chain import EnhancedRAGChain
    from ...rag.vector_store import ChromaDBManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["rag"], dependencies=[Depends(require_token)])

NOT_INDEXED_MESSAGE = (
    "This document isn't indexed yet. Ask again once it has been indexed."
)
NO_TEXT_MESSAGE = (
    "No text could be extracted from this PDF, so there is nothing to ask about."
)
# The `code` on `/rag/ask`'s `error` event when the question's index had to be
# cleared. The chat panel indexes the document again when it sees it.
INDEX_UNAVAILABLE = "index_unavailable"
INDEX_UNAVAILABLE_MESSAGE = (
    "This document's chat index was missing or damaged, so it has been cleared. "
    "Ask again once the document has been indexed."
)

_vector_store: Optional[ChromaDBManager] = None
_rag_chain: Optional[EnhancedRAGChain] = None
_init_lock = asyncio.Lock()

# Indexing and deleting are serialized per document. The chat panel starts an
# index job whenever a PDF is opened, so one document can be asked for twice at
# once; the second job waits, then finds the first one's index ready.
_document_locks: Dict[str, asyncio.Lock] = {}


def _document_lock(document_id: str) -> asyncio.Lock:
    lock = _document_locks.get(document_id)
    if lock is None:
        lock = _document_locks[document_id] = asyncio.Lock()
    return lock


def _build_chain() -> EnhancedRAGChain:
    """Blocking: opens ChromaDB and the embedding model, and reconciles the
    vector store with the records before anything can index."""
    from ...rag.rag_chain import EnhancedRAGChain
    from ...rag.vector_store import ChromaDBManager

    global _vector_store, _rag_chain
    store = ChromaDBManager()
    _recover(get_records_store(), store)
    _vector_store = store
    _rag_chain = EnhancedRAGChain(store)
    return _rag_chain


def _recover(records: RecordsStore, store: ChromaDBManager) -> None:
    """Make the vector store agree with the records. Blocking.

    Runs before this process can index anything, so an index still marked
    `indexing` belongs to a process that died partway through one: its row is
    failed and its half-written collection dropped. A collection no row
    accounts for — its index was replaced or deleted, and the drop didn't
    happen — is dropped as well. And a ready index whose collection is gone
    (`vectors/` deleted or replaced behind the records' back) loses its row: it
    would pass `/rag/ask`'s pre-flight and then fail every question (#31).

    Run it once per process, never again to pick up something new such as
    settings: an index being built at that moment is `indexing` too, and would
    be failed and dropped mid-write.
    """
    for index_id in records.fail_stale_indexing():
        store.drop_index(index_id)
    collections = store.index_ids()
    for index_id in collections - records.index_ids():
        store.drop_index(index_id)
    for index_id in records.index_ids(status="ready") - collections:
        records.delete_index(index_id)


def _load_document_processor() -> type:
    """Import the scientific-PDF processor (fitz + camelot + pdfplumber, ~4 s).

    Blocking, for the same reason `_build_chain` is, and reached the same way —
    only ever through `asyncio.to_thread`.
    """
    from ...rag.document_processor import ScientificPDFProcessor

    return ScientificPDFProcessor


async def _get_chain() -> EnhancedRAGChain:
    async with _init_lock:
        if _rag_chain is None:
            # First use loads the embedding model (seconds of CPU + disk).
            # Run it off the event loop so the rest of the sidecar — health
            # checks, translation SSE — stays responsive.
            await asyncio.to_thread(_build_chain)
        return _rag_chain


async def _get_store() -> ChromaDBManager:
    await _get_chain()
    assert _vector_store is not None
    return _vector_store


def _loaded_store() -> Optional[ChromaDBManager]:
    """The vector store if this process has opened it. Never opens it.

    Removing a document or resetting the indexes must not load chromadb and the
    embedding model just to delete. Collections the records no longer account
    for are dropped by `_recover` when the store does open.
    """
    return _vector_store


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


def _identify(path: Path) -> Tuple[str, int]:
    """A document's id — the SHA-256 of its bytes — and its size.

    Blocking: the hash streams the whole file, ~100-300 ms on a 50 MB PDF.
    """
    return compute_file_hash(path), path.stat().st_size


def _page_count(processor: object) -> Optional[int]:
    layouts = getattr(processor, "page_layouts", None)
    return len(layouts) if layouts else None


async def _abandon(
    records: RecordsStore,
    store: Optional[ChromaDBManager],
    index: Optional[IndexRecord],
    error: str,
) -> None:
    """Fail an index that didn't finish, and drop what it had written.

    Best-effort: anything this misses, `_recover` catches on the next start.
    """
    if index is None:
        return
    try:
        await asyncio.to_thread(records.fail_index, index.id, error)
        if store is not None:
            await asyncio.to_thread(store.drop_index, index.id)
    except Exception:  # noqa: BLE001
        logger.warning("Could not clean up the unfinished index %s", index.id, exc_info=True)


async def _run_index(job: Job, payload: IndexRequest) -> None:
    file_path = Path(payload.file_path)
    records = get_records_store()
    store: Optional[ChromaDBManager] = None
    building: Optional[IndexRecord] = None

    try:
        # A document is its bytes, not its name: a second, different
        # `paper.pdf` is a different document (#59).
        document_id, size = await asyncio.to_thread(_identify, file_path)
        # Named before the vector store loads, which takes seconds on first
        # use, so the chat panel can show the document's history meanwhile.
        await job.emit(
            "progress",
            {"stage": "Opening the document", "progress": 2, "document_id": document_id},
        )
        store = await _get_store()

        async with _document_lock(document_id):
            await job.emit("progress", {"stage": "Checking cache", "progress": 5})
            await asyncio.to_thread(
                records.upsert_document, document_id, file_path.name, size, str(file_path)
            )
            ready = await asyncio.to_thread(
                records.ready_index, document_id, EMBEDDING_MODEL, CHUNKER_VERSION
            )
            if ready is not None and not await asyncio.to_thread(store.has_index, ready.id):
                # Ready in the records, but its chunks are gone. Build it again
                # rather than hand the panel an index every question fails on.
                logger.warning(
                    "Index %s of document %s has no collection; indexing again",
                    ready.id, document_id,
                )
                await asyncio.to_thread(records.delete_index, ready.id)
                ready = None
            if ready is not None:
                await job.emit(
                    "progress",
                    {"stage": "Already indexed", "progress": 100, "chunks": ready.chunk_count},
                )
                await job.finish(
                    "done",
                    {"document_id": document_id, "chunks": ready.chunk_count, "cached": True},
                )
                return

            await job.emit("progress", {"stage": "Extracting text", "progress": 20})
            # Imported here rather than at the top of the job: the
            # already-indexed branch above returns without ever needing it. Off
            # the event loop for the same reason the extraction below is, and
            # inside the try so a failure still reaches the job's error event —
            # without a terminal SSE event the chat panel waits forever.
            ScientificPDFProcessor = await asyncio.to_thread(_load_document_processor)
            processor = ScientificPDFProcessor()
            # Blocking fitz/camelot/pdfplumber work — keep it off the event loop.
            chunks = await asyncio.to_thread(processor.process_pdf, file_path)

            if job.cancelled:
                await job.finish("cancelled", {})
                return
            if not chunks:
                await job.finish("error", {"message": NO_TEXT_MESSAGE})
                return

            building = await asyncio.to_thread(
                records.begin_index,
                document_id,
                EMBEDDING_MODEL,
                EMBEDDING_DIMENSIONS,
                CHUNKER_VERSION,
            )
            await job.emit("progress", {"stage": "Indexing", "progress": 70, "chunks": len(chunks)})
            await store.add_chunks(building.id, chunks)
            replaced = await asyncio.to_thread(
                records.complete_index, building.id, len(chunks), _page_count(processor)
            )
            building = None

            for old_index_id in replaced:
                try:
                    await asyncio.to_thread(store.drop_index, old_index_id)
                except Exception:  # noqa: BLE001 — `_recover` drops it next start
                    logger.warning(
                        "Could not drop the replaced index %s", old_index_id, exc_info=True
                    )

        await job.finish(
            "done", {"document_id": document_id, "chunks": len(chunks), "cached": False}
        )
    except asyncio.CancelledError:
        await _abandon(records, store, building, "Cancelled")
        await job.finish("cancelled", {})
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("RAG index job failed")
        await _abandon(records, store, building, str(exc))
        await job.finish("error", {"message": str(exc)})


@router.post("/index", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_index(payload: IndexRequest) -> JobAccepted:
    if not Path(payload.file_path).exists():
        raise HTTPException(status_code=400, detail=f"File not found: {payload.file_path}")
    registry = get_registry()
    job = await registry.create()
    job.task = asyncio.create_task(_run_index(job, payload))
    return JobAccepted(job_id=job.job_id)


@router.get("/index/{job_id}/events")
async def stream_index_events(
    job_id: str, last_seq: int = Query(0, ge=0)
) -> EventSourceResponse:
    registry = get_registry()
    if registry.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Unknown job_id")

    async def event_source():
        async for event in registry.stream(job_id, last_seq=last_seq):
            yield serialize_sse_event(event)

    return EventSourceResponse(event_source(), ping=15)


# ---------------------------------------------------------------------------
# Ask
# ---------------------------------------------------------------------------


async def _clear_unavailable_index(index: IndexRecord) -> None:
    """Delete an index whose chunks can't be read, so its document is indexed
    again. Best-effort: `_recover` catches what this misses on the next start."""
    try:
        await asyncio.to_thread(get_records_store().delete_index, index.id)
        store = await _get_store()
        await asyncio.to_thread(store.drop_index, index.id)
    except Exception:  # noqa: BLE001
        logger.warning("Could not clear the unavailable index %s", index.id, exc_info=True)


async def _save_exchange(document_id: str, question: str, answer: Dict[str, Any]) -> None:
    """Add a question and its answer to the document's chat history.

    Keyed by the document the question was accepted for, never by anything the
    client says afterwards, so an answer that arrives after the user opened
    another PDF is still filed under the one it was about. Best-effort: the
    answer is delivered even when saving fails, as it does when the document
    was removed mid-question.
    """
    try:
        await asyncio.to_thread(get_records_store().add_exchange, document_id, question, answer)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Could not save the chat history of document %s", document_id, exc_info=True
        )


async def _run_ask(
    job: Job,
    payload: AskRequest,
    index: IndexRecord,
    document_path: str,
    answer_lang: str,
) -> None:
    started = time.time()
    try:
        chain = await _get_chain()

        loop = asyncio.get_running_loop()

        def progress_callback(message: str, progress: int) -> None:
            # Bridge the synchronous callback into the running event loop.
            asyncio.run_coroutine_threadsafe(
                job.emit("progress", {"message": message, "progress": progress}), loop
            )

        result = await chain.answer_question(
            question=payload.question,
            index_id=index.id,
            document_id=index.document_id,
            document_path=document_path,
            answer_lang=answer_lang,
            max_pdf_sources=payload.max_pdf_sources,
            progress_callback=progress_callback,
        )

        # Saved before the answer is sent, so a chat panel that refetches the
        # history on `answer` finds it there.
        await _save_exchange(index.document_id, payload.question, result)
        await job.emit("answer", result)
        result["elapsed_seconds"] = time.time() - started
        await job.finish("done", result)
    except asyncio.CancelledError:
        await job.finish("cancelled", {})
        raise
    except IndexUnavailableError:
        logger.warning(
            "The chunks of index %s (document %s) are gone; clearing it",
            index.id, index.document_id,
        )
        await _clear_unavailable_index(index)
        await job.finish(
            "error", {"message": INDEX_UNAVAILABLE_MESSAGE, "code": INDEX_UNAVAILABLE}
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("RAG ask job failed")
        await job.finish("error", {"message": str(exc)})


@router.post("/ask", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_ask(payload: AskRequest) -> JobAccepted:
    # Refused before a job exists, like `/translate`'s pre-flight: a document
    # with no ready index has nothing to search. A records lookup, not a
    # ChromaDB one, so it costs nothing even before chat has been used.
    records = get_records_store()
    index = await asyncio.to_thread(
        records.ready_index, payload.document_id, EMBEDDING_MODEL, CHUNKER_VERSION
    )
    if index is None:
        raise HTTPException(status_code=409, detail=NOT_INDEXED_MESSAGE)
    document_path = await asyncio.to_thread(records.document_path, payload.document_id)
    # Resolved when the question is accepted, the way `/translate` resolves its
    # languages, so the answer is in the language chosen when it was asked.
    _, answer_lang = resolve_languages(get_settings(), None, payload.target_lang)

    registry = get_registry()
    job = await registry.create()
    job.task = asyncio.create_task(
        _run_ask(job, payload, index, document_path or "", answer_lang.value)
    )
    return JobAccepted(job_id=job.job_id)


@router.get("/ask/{job_id}/events")
async def stream_ask_events(
    job_id: str, last_seq: int = Query(0, ge=0)
) -> EventSourceResponse:
    registry = get_registry()
    if registry.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Unknown job_id")

    async def event_source():
        async for event in registry.stream(job_id, last_seq=last_seq):
            yield serialize_sse_event(event)

    return EventSourceResponse(event_source(), ping=15)


# ---------------------------------------------------------------------------
# Documents and chat history
# ---------------------------------------------------------------------------


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents() -> DocumentListResponse:
    """Every recorded document, most recently opened first.

    A records query: listing never loads the vector store or the embedding model.
    """
    records = get_records_store()
    documents = await asyncio.to_thread(
        records.list_documents, EMBEDDING_MODEL, CHUNKER_VERSION
    )
    return DocumentListResponse(
        documents=[
            DocumentSummaryResponse(
                document_id=document.id,
                display_name=document.display_name,
                path=document.path,
                size_bytes=document.size_bytes,
                page_count=document.page_count,
                chunk_count=document.chunk_count,
                question_count=document.question_count,
                last_opened_at=ms_to_iso(document.last_opened_at),
            )
            for document in documents
        ]
    )


@router.delete("/document/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: str) -> None:
    """Forget a document: its record, the paths it was opened from, its chat
    indexes and its chat history. Opening the PDF again starts afresh."""
    records = get_records_store()
    async with _document_lock(document_id):
        # 404 when the document isn't recorded, 500 when storage fails. The
        # store used to report both the other way round (#59).
        try:
            removed = await asyncio.to_thread(records.delete_document, document_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Removing document %s failed", document_id)
            raise HTTPException(
                status_code=500, detail=f"Could not remove the document: {exc}"
            ) from exc
        if removed is None:
            raise HTTPException(status_code=404, detail="Document not found")

        store = _loaded_store()
        if store is None:
            return  # `_recover` drops the collections when the store opens
        for index_id in removed:
            try:
                await asyncio.to_thread(store.drop_index, index_id)
            except Exception:  # noqa: BLE001 — the rows are gone; `_recover` drops it next start
                logger.warning("Could not drop the collection of index %s", index_id, exc_info=True)


@router.get("/document/{document_id}/messages", response_model=ChatHistoryResponse)
async def get_chat_history(document_id: str) -> ChatHistoryResponse:
    """One document's chat history, oldest first; empty when it has none."""
    messages = await asyncio.to_thread(get_records_store().messages, document_id)
    return ChatHistoryResponse(
        messages=[
            ChatMessageResponse(
                id=message.id,
                role=message.role,
                text=message.content,
                answer=message.answer,
                created_at=ms_to_iso(message.created_at),
            )
            for message in messages
        ]
    )


@router.delete(
    "/document/{document_id}/messages", status_code=status.HTTP_204_NO_CONTENT
)
async def clear_chat_history(document_id: str) -> None:
    await asyncio.to_thread(get_records_store().clear_messages, document_id)


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

RESET_FILES_FAILED_MESSAGE = (
    "The chat indexes were cleared, but their files could not be deleted. "
    "Restart PDFusion, then reset again."
)


def _drop_collections(
    records: RecordsStore, store: ChromaDBManager, index_ids: List[str]
) -> None:
    """Drop the deleted indexes' collections, and any other collection no row
    accounts for. Blocking."""
    for index_id in index_ids:
        store.drop_index(index_id)
    for index_id in store.index_ids() - records.index_ids():
        store.drop_index(index_id)


def _delete_vector_store() -> None:
    """Delete `vectors/` outright. Blocking.

    Only while this process has no client open on it. A client that failed to
    open can still be cached by chromadb, holding the store's files, so that
    cache is cleared first whenever chromadb has been imported at all.
    """
    shared = sys.modules.get("chromadb.api.shared_system_client")
    if shared is not None:
        shared.SharedSystemClient.clear_system_cache()
    vectors = appdata_dir() / "vectors"
    shutil.rmtree(vectors, ignore_errors=True)
    if vectors.exists():
        raise OSError(f"Could not delete {vectors}")


@router.post("/reset", response_model=ResetIndexesResponse)
async def reset_indexes() -> ResetIndexesResponse:
    """Delete every chat index and its chunks: the recovery action for a damaged
    vector store (#31).

    Documents and chat history stay, and each document is indexed again the
    next time chat opens it. An index being built right now is left to finish.
    Runs under `_init_lock`, so the store can't open partway through. When it
    isn't open, `vectors/` is deleted outright, which works even on a store too
    damaged to open.
    """
    records = get_records_store()
    async with _init_lock:
        try:
            removed = await asyncio.to_thread(records.delete_settled_indexes)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Resetting the chat indexes failed")
            raise HTTPException(
                status_code=500, detail=f"Could not reset the chat indexes: {exc}"
            ) from exc
        store = _loaded_store()
        try:
            if store is not None:
                await asyncio.to_thread(_drop_collections, records, store, removed)
            else:
                await asyncio.to_thread(_delete_vector_store)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Deleting the chat indexes' files failed")
            raise HTTPException(status_code=500, detail=RESET_FILES_FAILED_MESSAGE) from exc
    return ResetIndexesResponse(removed=len(removed))
