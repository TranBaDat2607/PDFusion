"""PDF → chat chunks, page by page, with what each page needs to be read.

Replaces `document_processor.ScientificPDFProcessor`, the old chunker. What that
one did, and why each part changed:

- It chunked spans, ~1000 characters at a time. The embedding model reads 128
  tokens (`onnx_embeddings._MAX_SEQ_LENGTH`), ~500 characters of English, so
  half of nearly every chunk was never embedded. Chunks here are paragraphs
  packed up to `MAX_CHUNK_CHARS`, split between sentences.
- Its tables came from Camelot (which needs Ghostscript, absent on most
  machines) and pdfplumber, called once per block that looked tabular — 17 s
  on a 15-page paper — and when both failed it wrote "Column 1 | Column 2 /
  Table data | not extracted" into the chunk, which chat then quoted as the
  document's table. PyMuPDF's `find_tables` reads ruled tables in-process, and
  a table it can't find stays as text.
- A scanned page has no text layer, so the old processor produced no chunks,
  and a scanned PDF could not be indexed at all ("No text could be extracted").
  Scanned pages are OCRed here (`rag/ocr.py`), when an OCR engine is available.
- Its pages had no notion of what they hold. Each page here gets a
  `page_kind` — `text`, `visual` (a chart, figure or table) or `scanned` — which
  is how the answer step knows which pages to show the model as images rather
  than as their text layer (`retrieval.select_image_pages`).
- Ligatures (`ﬁ`) and line-break hyphens stayed in the text, so "firmware"
  never matched a page that printed "ﬁrmware". Text is NFKC-normalised and
  dehyphenated.

Headings are kept as each chunk's `heading` rather than as chunks of their own:
retrieval prefixes it to the text it embeds and scores (`retrieval.contextual_text`), so
a chunk under "Products" matches a question about products even when its own
words don't say so.

Blocking and CPU-bound: call `process_pdf` through `asyncio.to_thread`.
"""

from __future__ import annotations

import logging
import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# Renders a page image (PNG bytes) into its lines of text, in reading order.
OcrEngine = Callable[[bytes], List[str]]

# ~128 tokens of English, the input limit of MiniLM, the first embedding
# model, less room for the heading `retrieval.contextual_text` puts in front.
# multilingual-e5-small reads 512, but 1200-character chunks retrieved worse
# with it (hit@1 0.87 → 0.83, `benchmarks/rag/`): a smaller chunk is a sharper
# match, and the answer reads its neighbours anyway (`rag_chain.NEIGHBOURS`).
MAX_CHUNK_CHARS = 600
# A table is kept whole up to here — its rows mean little apart from its
# header — and split into header-repeating parts past it. Past ~500 characters
# a part outruns the embedding window and is found by BM25 alone; splitting at
# 600 instead was measured with benchmarks/rag/ and changed no retrieval
# result, and a whole table is better context for the answer.
MAX_TABLE_CHARS = 2000

# A heading is set this much larger than the document's body text — or, in
# bold, this much (BERT's section headings: 12 pt bold over 10.9 pt, 1.10x).
_HEADING_RATIO = 1.15
_BOLD_HEADING_RATIO = 1.05
# A block set wholly in bold at the body's own size is a subsection title:
# papers set "4.1 GLUE" that way, on lines of its own. Only the whole block:
# bold words that open a paragraph and run into it are emphasis, not a title.
# Kept as a chunk's `subheading`, not its `heading`: headings are embedded with
# every chunk under them, and taking these as headings cost retrieval three
# questions in 99 (`benchmarks/rag/README.md`). The outline is what uses them.
# Just under 1.0, for a heading font a hair smaller than the body's.
_BOLD_BLOCK_RATIO = 0.98
# ...and is short: at most two lines and this many characters. A longer run
# of large text is a paragraph set large (the Attention paper's 12 pt
# permission notice), and stays text.
_HEADING_MAX_CHARS = 120
_HEADING_MAX_LINES = 2
# Fewer characters than this and a page has no usable text layer.
_MIN_TEXT_CHARS = 20
# Share of the page images must cover for it to count as a scan, or as visual.
_SCAN_COVERAGE = 0.5
_FIGURE_COVERAGE = 0.10
# Share of a detected grid's cells that must hold text for it to be a table.
_MIN_FILLED_CELLS = 0.5
# A caption line must sit this close (points) to its table.
_CAPTION_GAP = 40.0
# OCR render resolution. Word-level F1 of RapidOCR against the Attention
# paper's own text layer peaked here; 200 dpi gained 0.02 for 15% more time.
OCR_DPI = 150

_TABLE_CAPTION = re.compile(r"^(table|tab\.|bảng)\s*\d+", re.IGNORECASE)
_FIGURE_CAPTION = re.compile(r"^(figure|fig\.|hình)\s*\d+\s*[:.]", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")
_WHITESPACE = re.compile(r"\s+")

Rect = Tuple[float, float, float, float]


def normalize_text(text: str) -> str:
    """NFKC (ligatures, full-width forms), whitespace runs to one space."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


@dataclass
class _Line:
    text: str
    size: float
    bbox: Rect
    bold: bool = False
    # Rotated text — arXiv's id stamped up the margin — is never a heading.
    horizontal: bool = True


@dataclass
class _Block:
    lines: List[_Line]
    bbox: Rect

    @property
    def text(self) -> str:
        return normalize_text(" ".join(line.text for line in self.lines))


@dataclass
class _Page:
    number: int
    rect: fitz.Rect
    blocks: List[_Block] = field(default_factory=list)
    image_coverage: float = 0.0
    text_chars: int = 0


def _union(rects: Iterable[Rect]) -> Rect:
    rects = list(rects)
    return (
        float(min(r[0] for r in rects)),
        float(min(r[1] for r in rects)),
        float(max(r[2] for r in rects)),
        float(max(r[3] for r in rects)),
    )


def _clip(rect: Rect, page: fitz.Rect) -> Rect:
    return (
        float(max(rect[0], page.x0)),
        float(max(rect[1], page.y0)),
        float(min(rect[2], page.x1)),
        float(min(rect[3], page.y1)),
    )


def _inside(rect: Rect, area: Rect) -> bool:
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    return area[0] <= cx <= area[2] and area[1] <= cy <= area[3]


def _read_page(page: fitz.Page) -> _Page:
    # Ligatures expanded and line-break hyphens joined by PyMuPDF itself:
    # without `TEXT_PRESERVE_LIGATURES`, and with `TEXT_DEHYPHENATE`.
    flags = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP | fitz.TEXT_DEHYPHENATE
    data = page.get_text("dict", flags=flags)
    result = _Page(number=page.number, rect=page.rect)
    for block in data.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        lines: List[_Line] = []
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in line["spans"])
            direction = line.get("dir", (1.0, 0.0))
            lines.append(_Line(
                text=text,
                size=max(s["size"] for s in spans),
                bbox=tuple(line["bbox"]),
                bold=all(s.get("flags", 0) & fitz.TEXT_FONT_BOLD or "bold" in s.get("font", "").lower()
                         for s in spans),
                horizontal=abs(direction[0] - 1.0) < 0.01 and abs(direction[1]) < 0.01,
            ))
        if lines:
            result.blocks.append(_Block(lines=lines, bbox=tuple(block["bbox"])))
    result.text_chars = sum(len(b.text.replace(" ", "")) for b in result.blocks)

    area = abs(page.rect) or 1.0
    covered = 0.0
    for info in page.get_image_info():
        covered += abs(fitz.Rect(info["bbox"]) & page.rect)
    result.image_coverage = min(1.0, covered / area)
    return result


def _body_size(pages: List[_Page], table_areas: List[List[Rect]]) -> float:
    """The font size most of the document's characters are set in, tables
    aside: their cells are often set smaller, and a table-heavy document would
    otherwise make its body text look like headings."""
    sizes: List[float] = []
    for page, areas in zip(pages, table_areas):
        for block in page.blocks:
            if any(_inside(block.bbox, area) for area in areas):
                continue
            for line in block.lines:
                sizes.extend([round(line.size, 1)] * max(1, len(line.text.strip())))
    return statistics.median(sizes) if sizes else 0.0


def _pack(paragraphs: List[str], limit: int) -> List[str]:
    """Paragraphs packed into chunks of at most `limit` characters.

    Short paragraphs share a chunk; a long one is split between sentences,
    and a sentence longer than `limit` between words. Nothing is dropped.
    """
    pieces: List[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= limit:
            pieces.append(paragraph)
            continue
        for sentence in _SENTENCE_END.split(paragraph):
            if len(sentence) <= limit:
                pieces.append(sentence)
                continue
            words, current = sentence.split(" "), ""
            for word in words:
                while len(word) > limit:  # a "word" longer than a chunk: a URL, a hash
                    if current:
                        pieces.append(current)
                        current = ""
                    pieces.append(word[:limit])
                    word = word[limit:]
                candidate = f"{current} {word}" if current else word
                if len(candidate) > limit:
                    pieces.append(current)
                    current = word
                else:
                    current = candidate
            if current:
                pieces.append(current)

    chunks: List[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current} {piece}" if current else piece
        if len(candidate) > limit and current:
            chunks.append(current)
            current = piece
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _cell(value: Optional[str]) -> str:
    return normalize_text(value or "").replace("|", "\\|")


def _markdown_rows(rows: List[List[Optional[str]]]) -> Tuple[str, List[str]]:
    """GitHub-Markdown header (with its separator) and body rows.

    Built from `Table.extract()` rather than `Table.to_markdown()`, which
    HTML-escapes cell text: a "-2.3%" cell came out as "&#45;2.3%".
    """
    width = max(len(row) for row in rows)
    padded = [[_cell(c) for c in row] + [""] * (width - len(row)) for row in rows]
    header = "| " + " | ".join(padded[0]) + " |\n|" + "---|" * width
    body = ["| " + " | ".join(row) + " |" for row in padded[1:]]
    return header, body


class PdfIngestor:
    """Turns one PDF into chat chunks. One instance per document."""

    def __init__(self, ocr: Optional[OcrEngine] = None, max_chunk_chars: int = MAX_CHUNK_CHARS,
                 on_page: Optional[Callable[[int, int], None]] = None) -> None:
        self.ocr = ocr
        self.max_chunk_chars = max_chunk_chars
        # Called with (pages done, page count) after each page: OCR takes
        # seconds a page, and the chat panel shows how far indexing has got.
        self.on_page = on_page
        # One entry per page, chunks or not: `api/routes/rag.py:_page_count`
        # records the document's page count from its length.
        self.page_layouts: Dict[int, Dict[str, Any]] = {}
        self._stats: Dict[str, int] = {}
        self._ocr_failed = 0
        # The subsection in force (`_is_subheading`), carried across pages as
        # the heading is; a new heading ends it.
        self._subheading = ""

    def process_pdf(self, path: Path) -> List[Dict[str, Any]]:
        counts = dict.fromkeys(
            ("pages", "text_pages", "visual_pages", "scanned_pages", "ocr_pages",
             "ocr_failed_pages", "tables", "chunks"), 0
        )
        self._ocr_failed = 0
        self._subheading = ""
        chunks: List[Dict[str, Any]] = []
        with fitz.open(path) as doc:
            pages = [_read_page(page) for page in doc]
            tables = [self._tables(page) for page in doc]
            body = _body_size(pages, [[area for area, _ in t] for t in tables])
            heading = ""
            for page, info, page_tables in zip(doc, pages, tables):
                page_chunks, heading, kind, table_count = self._page_chunks(
                    page, info, page_tables, body, heading
                )
                counts["pages"] += 1
                counts[f"{kind}_pages"] += 1
                counts["tables"] += table_count
                if kind == "scanned" and any(c["metadata"]["kind"] == "ocr" for c in page_chunks):
                    counts["ocr_pages"] += 1
                self.page_layouts[page.number] = {
                    "width": page.rect.width,
                    "height": page.rect.height,
                    "page_kind": kind,
                }
                chunks.extend(page_chunks)
                if self.on_page is not None:
                    self.on_page(page.number + 1, doc.page_count)
        counts["chunks"] = len(chunks)
        counts["ocr_failed_pages"] = self._ocr_failed
        self._stats = counts
        logger.info("Ingested %s: %s", Path(path).name, counts)
        return chunks

    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    # ------------------------------------------------------------------

    def _page_chunks(
        self,
        page: fitz.Page,
        info: _Page,
        tables: List[Tuple[Rect, List[List[Optional[str]]]]],
        body: float,
        heading: str,
    ) -> Tuple[List[Dict[str, Any]], str, str, int]:
        """The page's chunks, the heading in force after it, its kind, and how
        many tables it held."""
        rect = info.rect
        if info.text_chars < _MIN_TEXT_CHARS and info.image_coverage >= _SCAN_COVERAGE:
            return self._scanned(page, heading), heading, "scanned", 0

        table_areas = [area for area, _ in tables]
        captions: Dict[int, str] = {}
        caption_blocks: set = set()
        for i, block in enumerate(info.blocks):
            if any(_inside(block.bbox, area) for area in table_areas):
                continue
            if _TABLE_CAPTION.match(block.text):
                for t, area in enumerate(table_areas):
                    near_above = 0 <= area[1] - block.bbox[3] <= _CAPTION_GAP
                    near_below = 0 <= block.bbox[1] - area[3] <= _CAPTION_GAP
                    if (near_above or near_below) and t not in captions:
                        captions[t] = block.text
                        caption_blocks.add(i)
                        break

        captioned_table = any(_TABLE_CAPTION.match(b.text) for b in info.blocks)
        captioned_figure = any(_FIGURE_CAPTION.match(b.text) for b in info.blocks)
        has_tables = bool(tables) or captioned_table
        has_figures = info.image_coverage >= _FIGURE_COVERAGE or (
            captioned_figure and (info.image_coverage > 0 or len(page.get_drawings()) >= 10)
        )
        kind = "visual" if (has_tables or has_figures) else "text"
        meta = {"page_kind": kind, "has_tables": has_tables, "has_figures": has_figures}

        # Text and tables in the order they sit on the page, so a table's
        # chunk lands between the paragraphs around it.
        items: List[Tuple[float, str, Any]] = []
        for i, block in enumerate(info.blocks):
            if i in caption_blocks or any(_inside(block.bbox, area) for area in table_areas):
                continue
            items.append((block.bbox[1], "block", block))
        for t, (area, rows) in enumerate(tables):
            items.append((area[1], "table", (area, rows, captions.get(t))))
        items.sort(key=lambda item: item[0])

        chunks: List[Dict[str, Any]] = []
        pending: List[Tuple[str, Rect]] = []

        def with_sub() -> Dict[str, Any]:
            return {**meta, "subheading": self._subheading}

        def flush() -> None:
            if not pending:
                return
            for text in _pack([p for p, _ in pending], self.max_chunk_chars):
                chunks.append(self._chunk(text, page.number, "text", heading,
                                          _clip(_union(r for _, r in pending), rect), with_sub()))
            pending.clear()

        for _, what, item in items:
            if what == "table":
                flush()
                area, rows, caption = item
                for text in self._table_texts(rows, caption):
                    chunks.append(self._chunk(text, page.number, "table", heading, _clip(area, rect),
                                              with_sub()))
                continue
            lead = self._heading_lines(item, body)
            if lead:
                flush()
                heading = normalize_text(" ".join(line.text for line in item.lines[:lead]))
                self._subheading = ""
            elif self._is_subheading(item, body):
                # Recorded, and nothing else: no flush, and its words stay in
                # the text. Splitting chunks at subsections moved retrieval
                # (two questions in 99 lost); this way the chunks are exactly
                # what they were, and only the outline learns the subsection.
                self._subheading = item.text
            body_lines = item.lines[lead:]
            if body_lines:
                text = normalize_text(" ".join(x.text for x in body_lines))
                if text:
                    pending.append((text, _union(x.bbox for x in body_lines)))
        flush()
        return chunks, heading, kind, len(tables)

    @staticmethod
    def _heading_lines(block: _Block, body: float) -> int:
        """How many of the block's first lines are a heading; 0 for none.

        Decided for the run as a whole, not line by line: taken a line at a
        time, a paragraph set large was read as a stack of headings, each
        replacing the last, and its text never reached a chunk.
        """
        if not body:
            return 0
        count = 0
        for line in block.lines:
            styled = line.horizontal and (
                line.size >= body * _HEADING_RATIO
                or (line.bold and line.size >= body * _BOLD_HEADING_RATIO)
            )
            if not styled:
                break
            count += 1
        text = normalize_text(" ".join(line.text for line in block.lines[:count]))
        if count > _HEADING_MAX_LINES or not 0 < len(text) < _HEADING_MAX_CHARS:
            return 0
        return count

    @staticmethod
    def _is_subheading(block: _Block, body: float) -> bool:
        """Whether the block is a subsection title: every line bold, at the
        body's size, short — "4.1 GLUE" on lines of its own."""
        if not body or not block.lines or len(block.lines) > _HEADING_MAX_LINES:
            return False
        if not all(line.horizontal and line.bold and line.size >= body * _BOLD_BLOCK_RATIO
                   for line in block.lines):
            return False
        return 0 < len(block.text) < _HEADING_MAX_CHARS

    def _tables(self, page: fitz.Page) -> List[Tuple[Rect, List[List[Optional[str]]]]]:
        try:
            found = page.find_tables()
        except Exception:  # noqa: BLE001 — a page whose layout confuses the finder is read as text
            logger.debug("Table detection failed on page %d", page.number, exc_info=True)
            return []
        tables = []
        for table in found.tables:
            rows = [row for row in table.extract() if any((cell or "").strip() for cell in row)]
            # One row, or one column, is a box around text, not a table.
            if len(rows) < 2 or max(len(r) for r in rows) < 2:
                continue
            # Nor is a grid that is mostly empty: that is a figure drawn with
            # lines, like the Attention paper's attention maps, and as a table
            # it is a wall of `|  |  |` with a word every few cells.
            cells = [cell for row in rows for cell in row]
            if sum(1 for cell in cells if (cell or "").strip()) < len(cells) * _MIN_FILLED_CELLS:
                continue
            tables.append((tuple(table.bbox), rows))
        return tables

    def _table_texts(self, rows: List[List[Optional[str]]], caption: Optional[str]) -> List[str]:
        header, body = _markdown_rows(rows)
        lead = f"{caption}\n{header}" if caption else header
        parts: List[str] = []
        current: List[str] = []
        for row in body:
            if current and len(lead) + sum(len(r) + 1 for r in current) + len(row) > MAX_TABLE_CHARS:
                parts.append("\n".join([lead, *current]))
                current = []
            current.append(row)
        parts.append("\n".join([lead, *current]))
        return parts

    def _scanned(self, page: fitz.Page, heading: str) -> List[Dict[str, Any]]:
        if self.ocr is None:
            return []
        png = page.get_pixmap(dpi=OCR_DPI).tobytes("png")
        try:
            lines = self.ocr(png)
        except Exception:  # noqa: BLE001 — one unreadable page shouldn't fail the document
            # Counted, so a scan whose every page failed can say so
            # (`api/routes/rag.py:_no_text_message`), and logged with the cause.
            logger.error("OCR failed on page %d", page.number + 1, exc_info=True)
            self._ocr_failed += 1
            return []
        text = normalize_text(" ".join(lines))
        if not text:
            return []
        meta = {"page_kind": "scanned", "has_tables": False, "has_figures": False}
        bbox = (float(page.rect.x0), float(page.rect.y0), float(page.rect.x1), float(page.rect.y1))
        return [
            self._chunk(piece, page.number, "ocr", heading, bbox, {**meta, "subheading": self._subheading})
            for piece in _pack([text], self.max_chunk_chars)
        ]

    @staticmethod
    def _chunk(text: str, page: int, kind: str, heading: str, bbox: Rect,
               meta: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "text": text,
            "page": page,
            "metadata": {"kind": kind, "heading": heading, "bbox": [float(v) for v in bbox], **meta},
        }
