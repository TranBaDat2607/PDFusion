"""OCR for scanned pages, so a scanned PDF can be indexed for chat.

RapidOCR (`rapidocr-onnxruntime`) is already installed with BabelDOC: ONNX
models of PaddleOCR's PP-OCRv4 (~16 MB), run by the onnxruntime the chat
embeddings use. No torch, no network, nothing to download.

Two things it needs help with, both measured on a rasterized copy of the
Attention paper against that paper's own text layer:

- Its recogniser is trained mostly on Chinese and drops the spaces between
  English words ("TheBoardofDirectorsapproved"). Word-level F1 was 0.33.
  `respace` puts a space wherever two characters sit further apart than a
  fifth of the line's height, and F1 rose to 0.64 (0.1×: 0.48, 0.3×: 0.48). A
  per-line Otsu threshold gained only 0.02 more.
- It is slow on CPU: ~9 s for a dense page at 150 dpi on an i5-12400F. It runs
  once, when the document is indexed, and only on pages with no text layer.

It reads Latin script without Vietnamese diacritics, which the keyword search
folds away anyway (`keyword_search.tokenize`). Whatever OCR gets wrong, the
answer model is shown the scanned page itself (`retrieval.select_image_pages`),
so OCR only has to be good enough to find the page.

`load_ocr_engine()` returns `None` when RapidOCR can't be imported; the
ingestor then indexes the pages that have text and skips the rest.
"""

from __future__ import annotations

import logging
import statistics
import threading
from typing import Any, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Gap between two characters, as a share of the line's height, past which
# they belong to different words. Tuned as the module doc says.
SPACE_GAP_RATIO = 0.2


def respace(chars: Sequence[str], boxes: Sequence[Any], ratio: float = SPACE_GAP_RATIO) -> str:
    """One recognised line, with a space wherever its characters' boxes leave
    a word-sized gap. `boxes` are RapidOCR's per-character quadrilaterals:
    four [x, y] corners, clockwise from the top left."""
    if not chars:
        return ""
    height = statistics.median(box[2][1] - box[0][1] for box in boxes) or 1
    out = [chars[0]]
    for i in range(1, len(chars)):
        char = chars[i]
        if char != " " and out[-1] != " " and boxes[i][0][0] - boxes[i - 1][1][0] > ratio * height:
            out.append(" ")
        out.append(char)
    return "".join(out)


class RapidOcrEngine:
    """`ingest.OcrEngine` backed by RapidOCR. Thread-safe; loads on first use."""

    def __init__(self) -> None:
        self._engine = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._engine is None:
                from rapidocr_onnxruntime import RapidOCR

                self._engine = RapidOCR()
            return self._engine

    def __call__(self, png: bytes) -> List[str]:
        engine = self._load()
        with self._lock:  # one onnxruntime session; RapidOCR is not reentrant
            result, _elapsed = engine(png, return_word_box=True)
        lines: List[str] = []
        for entry in result or []:
            # [box, text, score, char boxes, chars, char scores] with word boxes;
            # older releases return only the first three.
            if len(entry) >= 5 and entry[4]:
                lines.append(respace(entry[4], entry[3]))
            else:
                lines.append(entry[1])
        return lines


_engine: Optional[RapidOcrEngine] = None
_engine_lock = threading.Lock()


def load_ocr_engine() -> Optional[RapidOcrEngine]:
    """The process's OCR engine, or `None` when RapidOCR isn't installed.

    One per process, so two documents indexed at once share one set of models.
    """
    global _engine
    with _engine_lock:
        if _engine is None:
            try:
                import rapidocr_onnxruntime  # noqa: F401
            except ImportError:
                logger.warning("RapidOCR is not installed; scanned pages won't be indexed")
                return None
            _engine = RapidOcrEngine()
        return _engine
