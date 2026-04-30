"""PDF file streaming for the frontend pdf.js viewer."""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ..auth import require_token

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
