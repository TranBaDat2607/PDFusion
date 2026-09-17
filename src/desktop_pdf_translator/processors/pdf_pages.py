"""The PyMuPDF work around a translation: inspecting the input, splitting it
into chunks, and assembling the rolling PDF the viewer shows (#33).

`fitz` is imported inside each function. `api/routes/translation.py` calls
`inspect_pdf` for its pre-flight, and that module is imported while the
sidecar is still trying to print READY.

Split out of `processor.py`, which can't be imported without BabelDOC, so
this half can be tested against real PDFs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .exceptions import FileValidationError
from .page_selection import PageRange, Segment


@dataclass(frozen=True)
class PdfInfo:
    page_count: int
    size_mb: float


def inspect_pdf(path: Path) -> PdfInfo:
    """Page count and size, or `FileValidationError` saying why the file
    can't be translated."""
    import fitz  # PyMuPDF

    try:
        if not path.exists():
            raise FileValidationError(f"File does not exist: {path}")
        if path.suffix.lower() != ".pdf":
            raise FileValidationError(f"File is not a PDF: {path}")
        size_mb = path.stat().st_size / (1024 * 1024)
        try:
            with fitz.open(path) as doc:
                page_count = doc.page_count
        except Exception as exc:  # noqa: BLE001 — PyMuPDF raises several types
            raise FileValidationError(f"Cannot open PDF file: {exc}")
        if page_count == 0:
            raise FileValidationError("PDF has no pages")
        return PdfInfo(page_count=page_count, size_mb=size_mb)
    except FileValidationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise FileValidationError(f"File validation failed: {exc}")


def split_into_chunks(
    input_path: Path,
    chunks_dir: Path,
    runs: Sequence[PageRange],
) -> List[Tuple[Path, PageRange]]:
    """Write each run of pages to its own PDF.

    Returns `(chunk_path, (first, last))` per run, in the order given; pages
    are 1-indexed and inclusive.
    """
    import fitz  # PyMuPDF

    chunks_dir.mkdir(parents=True, exist_ok=True)
    chunks: List[Tuple[Path, PageRange]] = []
    with fitz.open(input_path) as src:
        for idx, (first, last) in enumerate(runs):
            chunk_path = chunks_dir / f"{input_path.stem}_chunk{idx:03d}.pdf"
            with fitz.open() as chunk_doc:
                chunk_doc.insert_pdf(src, from_page=first - 1, to_page=last - 1)
                chunk_doc.save(chunk_path)
            chunks.append((chunk_path, (first, last)))
    return chunks


def rebuild_rolling_pdf(
    rolling_path: Path,
    original_path: Path,
    segments: Sequence[Segment],
    chunk_results: Sequence[Optional[Path]],
) -> None:
    """Write a full-length PDF: the finished chunks' translated pages, and the
    original's pages everywhere else (`page_selection.rolling_segments`).

    The viewer always gets a document as long as the source, so its scroll
    position stays put as chunks land out of order, and pages outside the
    selection stay readable. `insert_pdf` copies objects without re-rendering,
    so this costs roughly the size of the document, once per chunk.
    """
    import fitz  # PyMuPDF

    with fitz.open() as merged, fitz.open(original_path) as src:
        for segment in segments:
            if segment.chunk is None:
                merged.insert_pdf(
                    src, from_page=segment.first - 1, to_page=segment.last - 1
                )
                continue
            translated = chunk_results[segment.chunk]
            if translated is None:
                raise ValueError(f"chunk {segment.chunk} has no translated PDF")
            with fitz.open(translated) as chunk_doc:
                merged.insert_pdf(chunk_doc)
        merged.save(rolling_path)
