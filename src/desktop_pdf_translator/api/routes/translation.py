"""Translation endpoints — start a job, stream progress, cancel."""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sse_starlette.sse import EventSourceResponse

from ...processors.events import EventType
from ...processors.processor import PDFProcessor
from ..auth import require_token
from ..jobs import get_registry, serialize_sse_event
from ..schemas import JobAccepted, TranslateRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/translate", tags=["translation"], dependencies=[Depends(require_token)])


async def _run_translation(job_id: str, payload: TranslateRequest) -> None:
    registry = get_registry()
    job = registry.get(job_id)
    if job is None:
        return

    file_path = Path(payload.file_path)
    output_dir = Path(payload.output_dir) if payload.output_dir else None
    processor = PDFProcessor()

    completion_data: dict = {}
    try:
        async for event in processor.process_pdf(
            file_path=file_path,
            source_lang=payload.source_lang,
            target_lang=payload.target_lang,
            translation_service=payload.service,
            output_dir=output_dir,
        ):
            if job.cancelled:
                await job.emit("cancelled", {})
                await job.finish("cancelled", {})
                return
            event_dict = event.to_dict()
            if event.type == EventType.FINISH:
                # CompletionEvent carries the translated_file path. Carry it
                # forward to the terminal `done` event so the React side
                # (useTranslation.ts) can render the translated PDF.
                completion_data = event_dict
                logger.info("Captured completion payload: %s", completion_data)
                continue
            await job.emit("progress", event_dict)
        logger.info("Emitting done event with payload: %s", completion_data)
        await job.finish("done", completion_data)
    except asyncio.CancelledError:
        await job.finish("cancelled", {})
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
