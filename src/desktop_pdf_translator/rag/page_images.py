"""Rendering the pages the chat's answer model is shown.

Rendered when a question is answered, from the PDF itself, rather than stored
when it is indexed: PyMuPDF renders a page in ~50 ms, and storing page images
for every indexed document would cost tens of megabytes each for pages that
are mostly never asked about. Only the few pages retrieval picked are ever
looked at (`retrieval.select_image_pages`).

Blocking: call through `asyncio.to_thread`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Sequence

import fitz  # PyMuPDF

from ..translators.base import PageImage

logger = logging.getLogger(__name__)

# The long side, in pixels. Anthropic downscales past 1568 px and OpenAI's high
# detail past 2048×768 tiles; ~1600 px keeps a 10 pt table readable on A4
# (~140 dpi) for ~1.5k tokens an image.
MAX_SIDE = 1600
JPEG_QUALITY = 80


def page_label(page: int) -> str:
    """The text sent just before a page's image (`PageImage.label`). The
    prompt cites pages as `[p. N]`; the label names the page the same way."""
    return f"[Image of page {page + 1}]"


def render_pages(path: Path, pages: Sequence[int], max_side: int = MAX_SIDE) -> List[PageImage]:
    """JPEGs of `pages` (0-indexed), in the order given.

    A file that can't be opened, or a page it doesn't have, gives no image
    instead of an error: the answer is still written from the pages' text.
    """
    try:
        doc = fitz.open(path)
    except Exception:  # noqa: BLE001 — moved, deleted, or unreadable since it was indexed
        logger.warning("Can't open %s to show its pages to the answer model", path)
        return []
    images: List[PageImage] = []
    with doc:
        for page_number in pages:
            if not 0 <= page_number < doc.page_count:
                continue
            page = doc[page_number]
            zoom = max_side / max(page.rect.width, page.rect.height)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            images.append(
                PageImage(
                    data=pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY),
                    mime_type="image/jpeg",
                    label=page_label(page_number),
                )
            )
    return images
