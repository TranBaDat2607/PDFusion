"""RAG endpoints — index a PDF, ask a question, stream events.

The `rag` package loads torch, chromadb, sentence-transformers and camelot
(~10 s). RAG is off by default, so most users never need any of it — every
import of it below lives inside the handler that needs it, and the sidecar
boots without paying for it.

A document is identified by the SHA-256 of its bytes, and every question is
about exactly one document (#59).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sse_starlette.sse import EventSourceResponse

from ...processors.pdf_cache import compute_file_hash
from ..auth import require_token
from ..jobs import Job, get_registry, serialize_sse_event
from ..schemas import AskRequest, IndexRequest, JobAccepted

if TYPE_CHECKING:
    from ...rag.rag_chain import EnhancedRAGChain
    from ...rag.vector_store import ChromaDBManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["rag"], dependencies=[Depends(require_token)])

NOT_INDEXED_MESSAGE = (
    "This document isn't indexed yet. Reopen it to index it, then ask again."
)

_vector_store: Optional[ChromaDBManager] = None
_rag_chain: Optional[EnhancedRAGChain] = None
_init_lock = asyncio.Lock()


def _build_chain() -> EnhancedRAGChain:
    """Blocking: constructs ChromaDB + loads the SentenceTransformer model."""
    from ...rag.rag_chain import EnhancedRAGChain
    from ...rag.vector_store import ChromaDBManager

    global _vector_store, _rag_chain
    _vector_store = ChromaDBManager()
    _rag_chain = EnhancedRAGChain(_vector_store)
    return _rag_chain


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


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


async def _run_index(job: Job, payload: IndexRequest) -> None:
    file_path = Path(payload.file_path)

    try:
        # A document is its bytes. The file name stem used to be the id, so a
        # second, different `paper.pdf` was reported "Already indexed" and
        # every answer about it came from the first one (#59). Hashing streams
        # the whole file (~100-300 ms on a 50 MB PDF), so it runs off the loop.
        document_id = await asyncio.to_thread(compute_file_hash, file_path)
        store = await _get_store()
        await job.emit("progress", {"stage": "Checking cache", "progress": 5})
        existing = await store.search_by_document(document_id)
        if existing:
            await job.emit(
                "progress",
                {"stage": "Already indexed", "progress": 100, "chunks": len(existing)},
            )
            await job.finish("done", {"document_id": document_id, "chunks": len(existing), "cached": True})
            return

        await job.emit("progress", {"stage": "Extracting text", "progress": 20})
        # Imported here rather than at the top of the job: the already-indexed
        # branch above returns without ever needing it. Off the event loop for
        # the same reason the extraction below is, and inside the try so a
        # failure still reaches the job's error event — without a terminal SSE
        # event the chat panel waits forever.
        ScientificPDFProcessor = await asyncio.to_thread(_load_document_processor)
        processor = ScientificPDFProcessor()
        # Blocking fitz/camelot/pdfplumber work — keep it off the event loop.
        chunks = await asyncio.to_thread(processor.process_pdf, file_path)

        if job.cancelled:
            await job.finish("cancelled", {})
            return

        await job.emit("progress", {"stage": "Indexing", "progress": 70, "chunks": len(chunks)})
        ok = await store.add_document_chunks(chunks, document_id, str(file_path))
        if not ok:
            await job.finish("error", {"message": "Failed to add chunks to vector store"})
            return

        await job.finish(
            "done", {"document_id": document_id, "chunks": len(chunks), "cached": False}
        )
    except asyncio.CancelledError:
        await job.finish("cancelled", {})
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("RAG index job failed")
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


async def _run_ask(job: Job, payload: AskRequest) -> None:
    started = time.time()
    try:
        chain = await _get_chain()

        # A document with no chunks is refused, not searched: the answer would
        # otherwise be "I could not find relevant information", as if the
        # document had been read and had nothing to say.
        if not await chain.vector_store.has_document(payload.document_id):
            await job.finish("error", {"message": NOT_INDEXED_MESSAGE})
            return

        loop = asyncio.get_running_loop()

        def progress_callback(message: str, progress: int) -> None:
            # Bridge the synchronous callback into the running event loop.
            asyncio.run_coroutine_threadsafe(
                job.emit("progress", {"message": message, "progress": progress}), loop
            )

        result = await chain.answer_question(
            question=payload.question,
            document_id=payload.document_id,
            max_pdf_sources=payload.max_pdf_sources,
            progress_callback=progress_callback,
        )

        await job.emit("answer", result)
        result["elapsed_seconds"] = time.time() - started
        await job.finish("done", result)
    except asyncio.CancelledError:
        await job.finish("cancelled", {})
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("RAG ask job failed")
        await job.finish("error", {"message": str(exc)})


@router.post("/ask", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_ask(payload: AskRequest) -> JobAccepted:
    registry = get_registry()
    job = await registry.create()
    job.task = asyncio.create_task(_run_ask(job, payload))
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


@router.delete("/document/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: str) -> None:
    store = await _get_store()
    # 404 means the store has no such document, and a storage failure is a
    # 500. The store used to report both the other way round: `True` for an id
    # that matched nothing, `False` only when it raised (#59).
    try:
        removed = await store.delete_document(document_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Removing document %s from the vector store failed", document_id)
        raise HTTPException(
            status_code=500, detail=f"Could not remove the document's index: {exc}"
        ) from exc
    if removed == 0:
        raise HTTPException(status_code=404, detail="Document not found")
