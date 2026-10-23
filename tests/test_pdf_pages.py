"""The PyMuPDF half of translating part of a PDF (#33), against real PDFs.

These used to be methods on `PDFProcessor`, which can't be imported without
BabelDOC, so splitting and reassembling a document had no test at all.
Every PDF here is generated, one line of text per page, so a page's origin can
be read back from its text.
"""

from __future__ import annotations

import gc
import struct
import os
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
    pages_without_text,
    rebuild_rolling_pdf,
    split_into_chunks,
    subset_embedded_fonts,
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


def test_a_pdf_that_wants_a_password_is_refused(tmp_path: Path):
    """`fitz.open` answers such a document instead of raising, and its
    `page_count` is 1, so nothing else here would notice."""
    locked = tmp_path / "locked.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), "secret")
        doc.save(locked, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="open-me")
    with pytest.raises(FileValidationError, match="password-protected"):
        inspect_pdf(locked)


def test_a_pdf_that_only_restricts_permissions_is_read(tmp_path: Path):
    """An owner password locks printing and copying, not reading. MuPDF opens
    it without one, and a translation of it works."""
    restricted = tmp_path / "restricted.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), "a paragraph long enough to count")
        doc.save(
            restricted,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            permissions=fitz.PDF_PERM_ACCESSIBILITY,
        )
    assert inspect_pdf(restricted).page_count == 1
    assert text_stats(restricted, None, min_length=5).paragraphs == 1


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
# pages_without_text
# ---------------------------------------------------------------------------


def _write_mixed_pdf(path: Path, builders: list) -> Path:
    """One page per builder, each given its page to draw on (or leave alone)."""
    with fitz.open() as doc:
        for build in builders:
            build(doc.new_page())
        doc.save(path)
    return path


def _with_text(text: str):
    return lambda page: page.insert_text((72, 72), text)


def _blank(page) -> None:
    pass


def _image_only(page) -> None:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
    page.insert_image(fitz.Rect(72, 72, 172, 172), pixmap=pix)


def test_a_page_with_text_is_not_returned(tmp_path: Path):
    pdf = _write_mixed_pdf(tmp_path / "text.pdf", [_with_text("a whole sentence")])
    assert pages_without_text(pdf, [1]) == frozenset()


def test_a_blank_page_is_returned(tmp_path: Path):
    pdf = _write_mixed_pdf(tmp_path / "blank.pdf", [_blank])
    assert pages_without_text(pdf, [1]) == frozenset({1})


def test_an_image_only_page_is_returned(tmp_path: Path):
    """A scan: ink on the page, but nothing for the translator to read."""
    pdf = _write_mixed_pdf(tmp_path / "scan.pdf", [_image_only])
    assert pages_without_text(pdf, [1]) == frozenset({1})


def test_a_page_whose_only_text_is_whitespace_is_returned(tmp_path: Path):
    pdf = _write_mixed_pdf(tmp_path / "spaces.pdf", [_with_text("   \t  ")])
    # Guard the fixture: the page must carry a text object, or this is just
    # the blank-page test again.
    with fitz.open(pdf) as doc:
        assert doc[0].get_text() != ""
        assert doc[0].get_text().strip() == ""
    assert pages_without_text(pdf, [1]) == frozenset({1})


def test_only_the_requested_pages_are_inspected(tmp_path: Path):
    pdf = _write_mixed_pdf(
        tmp_path / "three.pdf", [_with_text("first"), _blank, _with_text("third")]
    )
    assert pages_without_text(pdf, [1, 2, 3]) == frozenset({2})
    assert pages_without_text(pdf, [1, 3]) == frozenset()


def test_an_ocred_scan_is_not_returned(tmp_path: Path):
    """A scan with an invisible OCR layer has text BabelDOC translates; a
    screen that looked only at visible text would skip every such page."""

    def ocred(page) -> None:
        _image_only(page)
        page.insert_text((72, 72), "recognised words", render_mode=3)

    pdf = _write_mixed_pdf(tmp_path / "ocred.pdf", [ocred])
    assert pages_without_text(pdf, [1]) == frozenset()


def test_a_single_visible_character_is_enough_to_keep_a_page(tmp_path: Path):
    """A page number alone is still text BabelDOC gets to try."""
    pdf = _write_mixed_pdf(tmp_path / "numbered.pdf", [_with_text("7")])
    assert pages_without_text(pdf, [1]) == frozenset()


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


def _write_linked_pdf(path: Path, pages: int, links_per_page: int) -> Path:
    with fitz.open() as doc:
        for n in range(1, pages + 1):
            page = doc.new_page()
            page.insert_text((72, 72), f"original {n}")
            for k in range(links_per_page):
                top = 100 + 30 * k
                page.insert_link(
                    {
                        "kind": fitz.LINK_URI,
                        "from": fitz.Rect(72, top, 300, top + 20),
                        "uri": f"https://example.com/{n}/{k}",
                    }
                )
        doc.save(path)
    # Guard the fixture: without links this test proves nothing about them.
    with fitz.open(path) as doc:
        assert all(len(page.get_links()) == links_per_page for page in doc)
    return path


def test_rebuilding_the_rolling_pdf_over_and_over_leaves_no_objects_behind(
    tmp_path: Path,
):
    """A run rebuilds the rolling PDF after every chunk in one long-lived
    process, so anything a rebuild leaves alive piles up with the page count.
    Pages with links are the case that matters: a book is full of them
    (contents, cross-references, URLs), and a leaking `insert_pdf` leaves two
    lists behind for every link it copies."""
    linked = _write_linked_pdf(tmp_path / "linked.pdf", pages=10, links_per_page=4)
    chunks = split_into_chunks(linked, tmp_path / "in", contiguous_runs([3, 4, 8], 1))
    results = [_translate(chunks[0][0], tmp_path), None, _translate(chunks[2][0], tmp_path)]
    segments = rolling_segments(
        10, [r for _, r in chunks], [r is not None for r in results]
    )
    rebuilds = 20

    # The first call may fill caches that are meant to outlive it.
    rebuild_rolling_pdf(tmp_path / "warmup.pdf", linked, segments, results)
    gc.collect()
    before = len(gc.get_objects())

    for n in range(rebuilds):
        rebuild_rolling_pdf(tmp_path / f"rolling_{n}.pdf", linked, segments, results)
    gc.collect()
    growth = len(gc.get_objects()) - before

    assert growth < rebuilds, (
        f"{growth} objects left alive after {rebuilds} rebuilds of a linked PDF"
    )


# ---------------------------------------------------------------------------
# subset_embedded_fonts
# ---------------------------------------------------------------------------

# CJK as well as Latin: high glyph ids, so a subsetter that renumbered glyphs
# would draw the wrong ones. (The bundled font has no Vietnamese glyphs.)
_LINES = ["Hello world 123", "你好，世界", "日本語のテキスト"]


def _write_full_font_pdf(path: Path) -> Path:
    """One page per line, each embedding the whole of MuPDF's bundled CJK font.

    BabelDOC is told not to subset, so a page it typesets carries the full font;
    this reproduces that without depending on a system font (CI runs on Windows
    and Linux). A plain `doc.save` does not subset: the file comes out ~3.5 MB.
    """
    buffer = fitz.Font("cjk").buffer
    with fitz.open() as doc:
        for line in _LINES:
            page = doc.new_page()
            page.insert_font(fontname="F0", fontbuffer=buffer)
            page.insert_text((72, 72), line, fontname="F0")
        doc.save(path)
    return path


def _font_bytes(path: Path) -> int:
    """Total size of the embedded font programs, counting each font once."""
    total = 0
    with fitz.open(path) as doc:
        seen: set[int] = set()
        for page in doc:
            for font in page.get_fonts():
                if font[0] not in seen:
                    seen.add(font[0])
                    total += len(doc.extract_font(font[0])[3])
    return total


def _text(path: Path) -> list[str]:
    with fitz.open(path) as doc:
        return [page.get_text() for page in doc]


def _pixels(path: Path) -> list[bytes]:
    with fitz.open(path) as doc:
        return [page.get_pixmap(dpi=72).samples for page in doc]


@pytest.fixture
def full_font_pdf(tmp_path: Path) -> Path:
    path = _write_full_font_pdf(tmp_path / "page.pdf")
    # Guard the fixture: if this fails the tests below prove nothing.
    assert _font_bytes(path) > 3_000_000
    return path


def _glyf_bytes(path: Path) -> int:
    """Size of the `glyf` table (the outlines) of the first embedded font.

    Read from the sfnt table directory: 4-byte version, numTables at bytes 4-6,
    then 16-byte records of tag, checksum, offset, length from byte 12.
    """
    with fitz.open(path) as doc:
        font = doc.extract_font(doc[0].get_fonts()[0][0])[3]
    (num_tables,) = struct.unpack(">H", font[4:6])
    for n in range(num_tables):
        tag, _, _, length = struct.unpack(">4sIII", font[12 + 16 * n : 28 + 16 * n])
        if tag == b"glyf":
            return length
    raise AssertionError("embedded font has no glyf table")


def test_a_full_embedded_font_is_cut_down_to_its_glyphs(full_font_pdf: Path):
    """Asserts on the outlines, which are what "cut down to the glyphs used" cuts.

    Whole-file size is not a fair measure: a Type0/Identity-H font is addressed
    by glyph id, so per-glyph index tables (`loca`, `hmtx`) must keep one entry
    per id of the original font and cannot shrink with the glyphs used.
    """
    before = _glyf_bytes(full_font_pdf)

    assert subset_embedded_fonts(full_font_pdf) is True

    # The pages use a few dozen distinct glyphs out of ~50k; even with generous
    # slack for per-glyph size, the outlines must fall below 2% of the original.
    assert _glyf_bytes(full_font_pdf) < before * 0.02


def test_subsetting_leaves_the_text_unchanged(full_font_pdf: Path):
    before = _text(full_font_pdf)
    assert subset_embedded_fonts(full_font_pdf) is True
    assert _text(full_font_pdf) == before


def test_subsetting_leaves_the_rendering_unchanged(full_font_pdf: Path):
    """The glyphs the page uses must survive the cut, not just the text map."""
    before = _pixels(full_font_pdf)
    assert subset_embedded_fonts(full_font_pdf) is True
    assert _pixels(full_font_pdf) == before


def test_a_subsetting_failure_leaves_the_file_as_it_was(
    full_font_pdf: Path, monkeypatch: pytest.MonkeyPatch
):
    def boom(self, *args, **kwargs):
        raise RuntimeError("cannot subset")

    monkeypatch.setattr(fitz.Document, "subset_fonts", boom)
    before = full_font_pdf.read_bytes()

    assert subset_embedded_fonts(full_font_pdf) is False
    assert full_font_pdf.read_bytes() == before


def test_a_pdf_without_embedded_fonts_stays_a_valid_pdf(tmp_path: Path):
    path = _write_pdf(tmp_path / "plain.pdf", ["alpha", "beta"])

    subset_embedded_fonts(path)  # must not raise, whatever it returns

    assert _labels(path) == ["alpha", "beta"]


def test_a_locked_staged_file_still_leaves_the_original_as_it_was(
    full_font_pdf: Path, monkeypatch: pytest.MonkeyPatch
):
    """Windows: a scanner holding the freshly written copy makes both the
    replace and the clean-up of the copy fail. Neither may fail the chunk."""

    def locked(*args, **kwargs):
        raise PermissionError("in use by another process")

    monkeypatch.setattr(os, "replace", locked)
    monkeypatch.setattr(Path, "unlink", locked)
    before = full_font_pdf.read_bytes()

    assert subset_embedded_fonts(full_font_pdf) is False
    assert full_font_pdf.read_bytes() == before


def test_a_failed_replace_leaves_no_copy_behind(
    full_font_pdf: Path, monkeypatch: pytest.MonkeyPatch
):
    """The subset copy is written before it replaces the original, so a failure
    there is the one that can strand it next to the chunk."""

    def refused(*args, **kwargs):
        raise OSError("cannot replace")

    monkeypatch.setattr(os, "replace", refused)
    before = full_font_pdf.read_bytes()

    assert subset_embedded_fonts(full_font_pdf) is False
    assert full_font_pdf.read_bytes() == before
    assert sorted(full_font_pdf.parent.iterdir()) == [full_font_pdf]
