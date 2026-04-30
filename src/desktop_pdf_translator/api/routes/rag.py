"""RAG endpoints — index a PDF, ask a question, stream events."""

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sse_starlette.sse import EventSourceResponse

from ...rag.document_processor import ScientificPDFProcessor
from ...rag.rag_chain import EnhancedRAGChain
from ...rag.vector_store import ChromaDBManager
from ...rag.web_research import WebResearchEngine
from ..auth import require_token
from ..jobs import Job, get_registry
from ..schemas import AskRequest, IndexRequest, JobAccepted

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["rag"], dependencies=[Depends(require_token)])

# Lazily initialized singletons. The RAG stack is heavy (loads embedding models, etc.)
# so we only instantiate on first use.
_vector_store: Optional[ChromaDBManager] = None
_web_research: Optional[WebResearchEngine] = None
_rag_chain: Optional[EnhancedRAGChain] = None
_init_lock = asyncio.Lock()


async def _get_chain() -> EnhancedRAGChain:
    global _vector_store, _web_research, _rag_chain
    async with _init_lock:
        if _rag_chain is None:
            _vector_store = ChromaDBManager()
            _web_research = WebResearchEngine()
            _rag_chain = EnhancedRAGChain(_vector_store, _web_research)
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
    document_id = payload.document_id or file_path.stem

    try:
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
        processor = ScientificPDFProcessor()
        chunks = await processor.process_pdf(file_path)

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
async def stream_index_events(job_id: str) -> EventSourceResponse:
    registry = get_registry()
    if registry.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Unknown job_id")

    async def event_source():
        async for event in registry.stream(job_id):
            yield {"event": event["type"], "data": event["data"]}

    return EventSourceResponse(event_source(), ping=15)


# ---------------------------------------------------------------------------
# Ask
# ---------------------------------------------------------------------------


async def _run_ask(job: Job, payload: AskRequest) -> None:
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
            document_id=payload.document_id,
            include_web_research=payload.include_web_research,
            max_pdf_sources=payload.max_pdf_sources,
            max_web_sources=payload.max_web_sources,
            use_deep_search=payload.use_deep_search,
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
async def stream_ask_events(job_id: str) -> EventSourceResponse:
    registry = get_registry()
    if registry.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Unknown job_id")

    async def event_source():
        async for event in registry.stream(job_id):
            yield {"event": event["type"], "data": event["data"]}

    return EventSourceResponse(event_source(), ping=15)


@router.delete("/document/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: str) -> None:
    store = await _get_store()
    if not await store.delete_document(document_id):
        raise HTTPException(status_code=404, detail="Document not found")
