"""The PyMuPDF half of translating part of a PDF (#33), against real PDFs.

These used to be methods on `PDFProcessor`, which can't be imported without
BabelDOC, so splitting and reassembling a document had no test at all.
Every PDF here is generated, one line of text per page, so a page's origin can
be read back from its text.
"""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
import pytest

from desktop_pdf_translator.processors.exceptions import FileValidationError
from desktop_pdf_translator.processors.page_selection import (
    contiguous_runs,
    rolling_segments,
)
from desktop_pdf_translator.processors.pdf_pages import (
    inspect_pdf,
    rebuild_rolling_pdf,
    split_into_chunks,
    text_stats,
)


def _write_pdf(path: Path, labels: list[str]) -> Path:
    with fitz.open() as doc:
        for label in labels:
            page = doc.new_page()
            page.insert_text((72, 72), label)
        doc.save(path)
    return path


def _labels(path: Path) -> list[str]:
    with fitz.open(path) as doc:
        return [page.get_text().strip() for page in doc]


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return _write_pdf(tmp_path / "book.pdf", [f"original {n}" for n in range(1, 11)])


# ---------------------------------------------------------------------------
# inspect_pdf
# ---------------------------------------------------------------------------


def test_inspect_reports_pages_and_size(source: Path):
    info = inspect_pdf(source)
    assert info.page_count == 10
    assert info.size_mb == pytest.approx(source.stat().st_size / (1024 * 1024))


def test_a_missing_file_is_a_validation_error(tmp_path: Path):
    with pytest.raises(FileValidationError, match="does not exist"):
        inspect_pdf(tmp_path / "gone.pdf")


def test_a_file_without_the_pdf_extension_is_refused(tmp_path: Path):
    other = _write_pdf(tmp_path / "book.bin", ["x"])
    with pytest.raises(FileValidationError, match="not a PDF"):
        inspect_pdf(other)


def test_an_unreadable_pdf_is_refused(tmp_path: Path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    with pytest.raises(FileValidationError, match="Cannot open PDF file"):
        inspect_pdf(broken)


# ---------------------------------------------------------------------------
# text_stats
# ---------------------------------------------------------------------------


def test_text_is_counted_on_the_selected_pages_only(source: Path):
    everything = text_stats(source, None, 0)
    two = text_stats(source, [3, 7], 0)
    assert everything.paragraphs == 10
    assert two.paragraphs == 2
    # "original 3" and "original 7": ten characters each, none of them CJK.
    assert (two.cjk_chars, two.other_chars) == (0, 20)


def test_short_blocks_are_skipped_like_a_run_skips_them(tmp_path: Path):
    pdf = tmp_path / "short.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "4")
        page.insert_text((72, 400), "A sentence long enough to translate.")
        doc.save(pdf)
    assert text_stats(pdf, None, 5).paragraphs == 1
    assert text_stats(pdf, None, 0).paragraphs == 2


def test_images_are_not_paragraphs(tmp_path: Path):
    pdf = tmp_path / "picture.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
        page.insert_image(fitz.Rect(72, 72, 172, 172), pixmap=pix)
        doc.save(pdf)
    assert text_stats(pdf, None, 0).paragraphs == 0


def test_cjk_text_is_counted_as_such(tmp_path: Path):
    pdf = tmp_path / "japanese.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "山の天気", fontname="japan")
        doc.save(pdf)
    stats = text_stats(pdf, None, 0)
    assert (stats.paragraphs, stats.cjk_chars, stats.other_chars) == (1, 4, 0)


# ---------------------------------------------------------------------------
# split_into_chunks
# ---------------------------------------------------------------------------


def test_only_the_selected_pages_are_split_out(source: Path, tmp_path: Path):
    runs = contiguous_runs([3, 4, 8], 1)
    chunks = split_into_chunks(source, tmp_path / "in", runs)
    assert [page_range for _, page_range in chunks] == [(3, 3), (4, 4), (8, 8)]
    assert [_labels(path) for path, _ in chunks] == [
        ["original 3"],
        ["original 4"],
        ["original 8"],
    ]


def test_a_multi_page_chunk_keeps_its_pages_in_order(source: Path, tmp_path: Path):
    chunks = split_into_chunks(source, tmp_path / "in", [(2, 4)])
    assert _labels(chunks[0][0]) == ["original 2", "original 3", "original 4"]


# ---------------------------------------------------------------------------
# rebuild_rolling_pdf
# ---------------------------------------------------------------------------


def _translate(chunk: Path, tmp_path: Path) -> Path:
    """Stand in for BabelDOC: the same pages, relabelled."""
    labels = [label.replace("original", "translated") for label in _labels(chunk)]
    return _write_pdf(tmp_path / f"{chunk.stem}_translated.pdf", labels)


def test_the_rolling_pdf_is_as_long_as_the_source(source: Path, tmp_path: Path):
    """Pages outside the selection and chunks still pending both show the
    original, so the viewer's page count never changes mid-run."""
    chunks = split_into_chunks(source, tmp_path / "in", contiguous_runs([3, 4, 8], 1))
    results = [_translate(chunks[0][0], tmp_path), None, _translate(chunks[2][0], tmp_path)]
    segments = rolling_segments(
        10, [r for _, r in chunks], [r is not None for r in results]
    )
    rolling = tmp_path / "book_translated_v002.pdf"

    rebuild_rolling_pdf(rolling, source, segments, results)

    assert _labels(rolling) == [
        "original 1",
        "original 2",
        "translated 3",
        "original 4",
        "original 5",
        "original 6",
        "original 7",
        "translated 8",
        "original 9",
        "original 10",
    ]


def test_every_chunk_done_is_the_whole_translation(source: Path, tmp_path: Path):
    chunks = split_into_chunks(source, tmp_path / "in", contiguous_runs(range(1, 11), 3))
    results = [_translate(path, tmp_path) for path, _ in chunks]
    segments = rolling_segments(10, [r for _, r in chunks], [True] * len(chunks))
    rolling = tmp_path / "book_translated_v004.pdf"

    rebuild_rolling_pdf(rolling, source, segments, results)

    assert _labels(rolling) == [f"translated {n}" for n in range(1, 11)]


def test_a_segment_without_its_translation_is_a_bug(source: Path, tmp_path: Path):
    segments = rolling_segments(10, [(1, 1)], [True])
    with pytest.raises(ValueError):
        rebuild_rolling_pdf(tmp_path / "out.pdf", source, segments, [None])
