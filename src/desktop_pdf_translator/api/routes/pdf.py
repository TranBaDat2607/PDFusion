"""PDF file streaming + export for the frontend."""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ...utils.file_export import ExportError, export_pdf
from ..auth import require_token
from ..schemas import ExportPdfRequest, ExportPdfResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pdf", tags=["pdf"], dependencies=[Depends(require_token)])


@router.get("/file")
async def stream_pdf(path: str = Query(..., description="Absolute path to the PDF")) -> FileResponse:
    file = Path(path)
    if not file.exists() or not file.is_file():
        raise HTTPException(status_code=404, detail=f"PDF not found: {path}")
    if file.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Only .pdf files are served")
    return FileResponse(
        file,
        media_type="application/pdf",
        headers={
            # Allow pdf.js to fetch arbitrary byte ranges
            "Accept-Ranges": "bytes",
        },
    )


@router.post("/export", response_model=ExportPdfResponse)
async def export_translated_pdf(payload: ExportPdfRequest) -> ExportPdfResponse:
    """Copy a translated PDF to a permanent, user-chosen location.

    The frontend picks `destination_path` with the native Save dialog, so the
    user has already agreed to the location (and to overwriting, if the file
    exists). All we do here is make a durable copy — everything the pipeline
    itself produces lives in a temp dir or an evictable cache.

    Runs on a worker thread: a large PDF copy would otherwise stall the event
    loop and stutter any in-flight SSE stream.
    """
    try:
        result = await asyncio.to_thread(
            export_pdf,
            Path(payload.source_path),
            Path(payload.destination_path),
            Path(payload.protect_path) if payload.protect_path else None,
        )
    except ExportError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    return ExportPdfResponse(
        saved_path=str(result.saved_path),
        bytes_written=result.bytes_written,
    )
