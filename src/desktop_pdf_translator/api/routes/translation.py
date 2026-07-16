"""Translation endpoints — start a job, stream progress, cancel."""

import asyncio
import logging
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sse_starlette.sse import EventSourceResponse

from ...config import TranslationService, get_settings
from ...processors.events import EventType
from ...processors.processor import PDFProcessor
from ...translators import TranslatorFactory
from ..auth import require_token
from ..jobs import get_registry, serialize_sse_event
from ..schemas import (
    JobAccepted,
    PrewarmRequest,
    PrewarmResponse,
    TranslateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/translate", tags=["translation"], dependencies=[Depends(require_token)])


def _build_cancel_payload(processor: PDFProcessor, file_path: Path) -> dict:
    """Resolve the latest partial rolling PDF, drop older versions, and shape
    the payload like the `done` event so the React side can reuse the same
    completion-handling path."""
    partial = processor.get_partial_translated_file()
    processor.cleanup_partial_artifacts()
    return {
        "translated_file": str(partial) if partial else None,
        "original_file": str(file_path),
    }


async def _run_translation(job_id: str, payload: TranslateRequest) -> None:
    registry = get_registry()
    job = registry.get(job_id)
    if job is None:
        return

    file_path = Path(payload.file_path)
    output_dir = Path(payload.output_dir) if payload.output_dir else None
    processor = PDFProcessor()
    # Expose the processor so the reprioritize endpoint can reach it via the
    # job registry. Cleared automatically when the job is discarded.
    job.processor = processor

    completion_data: dict = {}
    try:
        async for event in processor.process_pdf(
            file_path=file_path,
            source_lang=payload.source_lang,
            target_lang=payload.target_lang,
            translation_service=payload.service,
            output_dir=output_dir,
            visible_page=payload.visible_page,
            bypass_cache=payload.bypass_cache,
        ):
            if job.cancelled:
                await job.finish("cancelled", _build_cancel_payload(processor, file_path))
                return
            event_dict = event.to_dict()
            if event.type == EventType.FINISH:
                # CompletionEvent carries the translated_file path. Carry it
                # forward to the terminal `done` event so the React side
                # (useTranslation.ts) can render the translated PDF.
                completion_data = event_dict
                logger.info("Captured completion payload: %s", completion_data)
                continue
            if event.type == EventType.CHUNK_READY:
                # Streaming-render: tell the React side a new rolling PDF
                # version has landed on disk. The frontend `useTranslation`
                # hook handles `chunk_ready` separately from `progress` so
                # the PdfViewer can hot-swap to the latest rolling file.
                logger.info(
                    "Emitting chunk_ready: chunk=%s/%s rolling=%s",
                    event_dict.get("chunk_index"),
                    event_dict.get("total_chunks"),
                    event_dict.get("rolling_pdf_path"),
                )
                await job.emit("chunk_ready", event_dict)
                continue
            if event.type == EventType.PARAGRAPH_TRANSLATED:
                # Throttled live preview — the processor's ticker emits at
                # ~5 Hz, picking only the most recent paragraph each tick.
                await job.emit("paragraph_translated", event_dict)
                continue
            await job.emit("progress", event_dict)
        logger.info("Emitting done event with payload: %s", completion_data)
        await job.finish("done", completion_data)
    except asyncio.CancelledError:
        await job.finish("cancelled", _build_cancel_payload(processor, file_path))
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Translation job %s failed", job_id)
        await job.finish("error", {"message": str(exc)})


@router.post("", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_translation(payload: TranslateRequest) -> JobAccepted:
    if not Path(payload.file_path).exists():
        raise HTTPException(status_code=400, detail=f"File not found: {payload.file_path}")
    registry = get_registry()
    job = await registry.create()
    job.task = asyncio.create_task(_run_translation(job.job_id, payload))
    return JobAccepted(job_id=job.job_id)


@router.get("/{job_id}/events")
async def stream_translation_events(job_id: str) -> EventSourceResponse:
    registry = get_registry()
    if registry.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Unknown job_id")

    async def event_source():
        async for event in registry.stream(job_id):
            yield serialize_sse_event(event)

    return EventSourceResponse(event_source(), ping=15)


@router.post("/{job_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_translation(job_id: str) -> None:
    registry = get_registry()
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job_id")
    job.cancel()


@router.post("/{job_id}/reprioritize", status_code=status.HTTP_204_NO_CONTENT)
async def reprioritize_translation(job_id: str, visible_page: int) -> None:
    """Update the priority anchor as the user scrolls. Pending chunks closest
    to `visible_page` will be picked next. In-flight chunks finish; this is
    best-effort. Silently no-ops if the job has already finished."""
    registry = get_registry()
    job = registry.get(job_id)
    if job is None or job.finished or job.processor is None:
        return
    try:
        await job.processor.reprioritize(visible_page)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("Reprioritize for %s failed: %s", job_id, exc)


# ---------------------------------------------------------------------------
# Pre-warm — fire on PDF open so the first Translate click feels instant
# ---------------------------------------------------------------------------

# (service, lang_in, lang_out) → already warmed in this sidecar process.
_WARMED: set[tuple[str, str, str]] = set()
# Keys with a warm-up currently in flight, so concurrent /prewarm calls don't
# schedule duplicate work. Removed on completion (success or failure).
_WARMING: set[tuple[str, str, str]] = set()
_WARM_LOCK = threading.Lock()

# Strong refs to in-flight prewarm tasks. asyncio.create_task only holds a weak
# reference, so without this the GC can reap a scheduled warm-up mid-flight.
_PREWARM_TASKS: set[asyncio.Task] = set()


def _warm_translator(
    service: TranslationService, lang_in: str, lang_out: str
) -> tuple[bool, str]:
    """Blocking warm-up. Runs in a worker thread (asyncio.to_thread) so the
    endpoint returns immediately even when Argos has to download its ~80 MB
    pack. Returns (ok, message); a failed warm-up must NOT be recorded as
    warm so the next /prewarm can retry (e.g. transient network failure)."""
    try:
        translator = TranslatorFactory.create_translator(
            service=service, lang_in=lang_in, lang_out=lang_out
        )
        # Argos: kicks off pack install via translate's preamble. We do not
        # actually call translate() here — _ensure_en_vi_installed is invoked
        # from `validate_configuration` too, and is fast once installed.
        try:
            from ...translators.argos_translator import _ensure_en_vi_installed
            if service == TranslationService.ARGOS:
                _ensure_en_vi_installed()
        except Exception:
            pass
        # Best-effort credential probe for LLMs — silently swallow because
        # this is fire-and-forget pre-warm; the real translate flow will
        # surface any errors via SSE.
        if service != TranslationService.ARGOS:
            try:
                translator.validate_configuration()
            except Exception:
                pass
        return True, f"Warmed {service.value}"
    except Exception as e:
        logger.warning("Pre-warm failed for %s: %s", service, e)
        return False, f"Pre-warm failed: {e}"


@router.post("/prewarm", response_model=PrewarmResponse)
async def prewarm(payload: PrewarmRequest) -> PrewarmResponse:
    settings = get_settings()
    service = payload.service or settings.translation.preferred_service
    # Soft-fallback to Argos when LLM has no key, matching processor logic.
    if service != TranslationService.ARGOS and not settings.has_api_key(service):
        service = TranslationService.ARGOS

    key = (service.value, payload.source_lang.value, payload.target_lang.value)
    with _WARM_LOCK:
        already = key in _WARMED
        in_flight = key in _WARMING
        if not already and not in_flight:
            _WARMING.add(key)

    if already:
        return PrewarmResponse(
            service=service.value, warmed=True, cached=True, message="Already warm"
        )
    if in_flight:
        return PrewarmResponse(
            service=service.value,
            warmed=False,
            cached=False,
            message="Warm-up already in progress",
        )

    # Don't block the HTTP response on the pack download. Schedule and return.
    async def _run() -> None:
        try:
            ok, msg = await asyncio.to_thread(
                _warm_translator,
                service,
                payload.source_lang.value,
                payload.target_lang.value,
            )
        except Exception as exc:  # noqa: BLE001 — never leave key stuck in _WARMING
            ok, msg = False, f"Pre-warm crashed: {exc}"
        with _WARM_LOCK:
            _WARMING.discard(key)
            if ok:
                _WARMED.add(key)
        logger.info("Pre-warm complete: %s (%s)", service.value, msg)

    task = asyncio.create_task(_run())
    _PREWARM_TASKS.add(task)
    task.add_done_callback(_PREWARM_TASKS.discard)
    return PrewarmResponse(
        service=service.value,
        warmed=False,
        cached=False,
        message="Warm-up scheduled",
    )
