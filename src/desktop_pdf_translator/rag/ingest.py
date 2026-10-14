"""PDF → chat chunks, page by page, with what each page needs to be read.

Stub: the signatures the tests are written against.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Renders a page image (PNG bytes) into its lines of text, in reading order.
OcrEngine = Callable[[bytes], List[str]]

MAX_CHUNK_CHARS = 600


class PdfIngestor:
    def __init__(self, ocr: Optional[OcrEngine] = None, max_chunk_chars: int = MAX_CHUNK_CHARS) -> None:
        self.ocr = ocr
        self.max_chunk_chars = max_chunk_chars
        self.page_layouts: Dict[int, Dict[str, Any]] = {}

    def process_pdf(self, path: Path) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def stats(self) -> Dict[str, int]:
        raise NotImplementedError
