"""PDF -> chat chunks (`rag.ingest.PdfIngestor`).

Every PDF is built here with PyMuPDF under `tmp_path`, so each test states in
its fixture exactly what the page holds (text, heading, ruled table, image,
scan). Text goes in through `insert_htmlbox`: `insert_textbox` with a TTF font
reads back with no-break spaces, which would make the normalisation tests
pass or fail for the wrong reason.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import fitz
import pytest
from PIL import Image, ImageDraw

from desktop_pdf_translator.rag.ingest import PdfIngestor

PAGE_W, PAGE_H = 595.0, 842.0


# ---------------------------------------------------------------- fixtures


def _png(width: int = 400, height: int = 300) -> bytes:
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((20, 20, width - 20, height - 20), outline="black", width=4)
    draw.line((20, height - 20, width - 20, 20), fill="red", width=6)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _write_html(page: fitz.Page, rect: fitz.Rect, html: str) -> None:
    page.insert_htmlbox(rect, html)


def _text_page(
    doc: fitz.Document,
    paragraphs: Sequence[str],
    heading: Optional[str] = None,
) -> fitz.Page:
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    top = 60.0
    if heading:
        _write_html(
            page,
            fitz.Rect(50, top, 545, top + 40),
            f'<p style="font-size:18pt">{heading}</p>',
        )
        top += 50
    body = "".join(f'<p style="font-size:11pt">{p}</p>' for p in paragraphs)
    _write_html(page, fitz.Rect(50, top, 545, 760), body)
    return page


def _table_page(
    doc: fitz.Document,
    rows: List[List[str]],
    caption_above: Optional[str] = None,
    caption_below: Optional[str] = None,
    intro: Optional[str] = None,
) -> fitz.Page:
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    if intro:
        _write_html(
            page,
            fitz.Rect(50, 50, 545, 100),
            f'<p style="font-size:11pt">{intro}</p>',
        )
    x0, y0, col_w, row_h = 72.0, 200.0, 140.0, 22.0
    if caption_above:
        _write_html(
            page,
            fitz.Rect(x0, y0 - 28, x0 + 3 * col_w, y0 - 4),
            f'<p style="font-size:10pt">{caption_above}</p>',
        )
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            rect = fitz.Rect(
                x0 + c * col_w, y0 + r * row_h,
                x0 + (c + 1) * col_w, y0 + (r + 1) * row_h,
            )
            page.draw_rect(rect, color=(0, 0, 0), width=0.6)
            _write_html(
                page,
                rect + (3, 2, -3, -2),
                f'<p style="font-size:10pt">{cell}</p>',
            )
    if caption_below:
        bottom = y0 + len(rows) * row_h
        _write_html(
            page,
            fitz.Rect(x0, bottom + 4, x0 + 3 * col_w, bottom + 28),
            f'<p style="font-size:10pt">{caption_below}</p>',
        )
    return page


def _image_page(doc: fitz.Document, caption: str = "A page with a chart.") -> fitz.Page:
    page = _text_page(doc, [caption])
    page.insert_image(fitz.Rect(72, 250, 523, 588), stream=_png())
    return page


def _scanned_page(doc: fitz.Document, lines: Sequence[str]) -> fitz.Page:
    """A page that is only a picture of text, as a scanner makes it."""
    donor = fitz.open()
    _text_page(donor, list(lines))
    png = donor[0].get_pixmap(dpi=100).tobytes("png")
    donor.close()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_image(page.rect, stream=png)
    return page


def _save(doc: fitz.Document, tmp_path: Path, name: str = "doc.pdf") -> Path:
    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return path


class FakeOcr:
    def __init__(self, lines: Sequence[str]) -> None:
        self.lines = list(lines)
        self.calls: List[bytes] = []

    def __call__(self, png: bytes) -> List[str]:
        self.calls.append(png)
        return list(self.lines)


def _ingest(path: Path, **kwargs: Any):
    ingestor = PdfIngestor(**kwargs)
    return ingestor, ingestor.process_pdf(path)


def _words(text: str) -> List[str]:
    return re.findall(r"\w+", text)


def _all_text(chunks: List[Dict[str, Any]]) -> str:
    return " ".join(c["text"] for c in chunks)


def _on_page(chunks: List[Dict[str, Any]], page: int) -> List[Dict[str, Any]]:
    return [c for c in chunks if c["page"] == page]


# ------------------------------------------------------------------- tests


def test_text_chunks_carry_their_zero_indexed_page(tmp_path: Path) -> None:
    doc = fitz.open()
    _text_page(doc, ["The first page talks about apples."])
    _text_page(doc, ["The second page talks about bananas."])
    _text_page(doc, ["The third page talks about cherries."])
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path)

    hit = [c for c in chunks if "cherries" in c["text"]]
    assert hit and all(c["page"] == 2 and c["metadata"]["kind"] == "text" for c in hit)
    assert all(c["page"] == 0 for c in chunks if "apples" in c["text"])


def test_ligature_is_normalised_to_plain_letters(tmp_path: Path) -> None:
    doc = fitz.open()
    page = _text_page(doc, ["The ﬁnal result was a ﬁne one."])
    assert "ﬁ" in page.get_text(), "fixture must really contain the ligature"
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path)

    text = _all_text(chunks)
    assert "final result" in text and "fine one" in text
    assert "ﬁ" not in text


def test_word_hyphenated_across_a_line_break_is_joined(tmp_path: Path) -> None:
    doc = fitz.open()
    page = _text_page(doc, ["The system gave strong perfor-<br>mance on every task."])
    assert "perfor-" in page.get_text(), "fixture must really break the word"
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path)

    text = _all_text(chunks)
    assert "performance" in text
    assert "perfor-" not in text


def test_runs_of_whitespace_collapse_to_one_space(tmp_path: Path) -> None:
    doc = fitz.open()
    _text_page(doc, ["Alpha beta<br>gamma<br>delta epsilon."])
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path)

    text = _all_text(chunks)
    assert "Alpha beta gamma delta epsilon." in text
    assert not re.search(r"\s{2,}|\n", text)


def test_no_chunk_exceeds_the_limit_and_no_word_is_lost_or_split(tmp_path: Path) -> None:
    sentences = [
        f"Sentence number {i} explains the quarterly figure {i * 7} in some detail."
        for i in range(1, 15)
    ]
    doc = fitz.open()
    _text_page(doc, [" ".join(sentences)])
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path, max_chunk_chars=200)

    assert len(chunks) > 1
    assert all(len(c["text"]) <= 200 for c in chunks)
    assert _words(_all_text(chunks)) == _words(" ".join(sentences))


def test_long_paragraph_splits_at_sentence_ends(tmp_path: Path) -> None:
    sentences = [f"Claim {i} is supported by the evidence below." for i in range(1, 13)]
    doc = fitz.open()
    _text_page(doc, [" ".join(sentences)])
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path, max_chunk_chars=200)

    assert len(chunks) > 1
    # Splitting between sentences keeps each one whole, so every chunk ends on a full stop.
    assert all(c["text"].rstrip().endswith(".") for c in chunks)


def test_short_paragraphs_on_a_page_merge_into_one_chunk(tmp_path: Path) -> None:
    doc = fitz.open()
    _text_page(doc, ["Apples are red.", "Bananas are yellow.", "Cherries are dark."])
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path)

    assert len(chunks) == 1
    text = chunks[0]["text"]
    assert "Apples" in text and "Bananas" in text and "Cherries" in text


def test_chunks_never_span_two_pages(tmp_path: Path) -> None:
    doc = fitz.open()
    _text_page(doc, ["Short note about zebras."])
    _text_page(doc, ["Short note about yaks."])
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path)

    assert len(chunks) == 2
    for c in chunks:
        assert not ("zebras" in c["text"] and "yaks" in c["text"])
    assert [c["page"] for c in chunks] == [0, 1]


def test_chunks_come_back_in_reading_order(tmp_path: Path) -> None:
    doc = fitz.open()
    for n in range(3):
        _text_page(doc, [f"Page body {n}."])
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path)

    pages = [c["page"] for c in chunks]
    assert pages and pages == sorted(pages)


def test_heading_is_not_a_chunk_but_labels_the_chunks_after_it(tmp_path: Path) -> None:
    doc = fitz.open()
    _text_page(doc, ["Opening words before any heading."])
    _text_page(doc, ["Details about the method are given here."], heading="Methods")
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path)

    assert not any(c["text"].strip() == "Methods" for c in chunks)
    assert not any(c["text"].startswith("Methods") for c in chunks)
    before = [c for c in chunks if "Opening" in c["text"]]
    after = [c for c in chunks if "Details" in c["text"]]
    assert before and all(c["metadata"]["heading"] == "" for c in before)
    assert after and all(c["metadata"]["heading"] == "Methods" for c in after)


def test_heading_carries_onto_the_next_page_until_replaced(tmp_path: Path) -> None:
    doc = fitz.open()
    _text_page(doc, ["Intro body text lives here."], heading="Introduction")
    _text_page(doc, ["Continuing body text without a new heading."])
    _text_page(doc, ["Findings are summarised in this part."], heading="Results")
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path)

    def heading_of(word: str) -> List[str]:
        return [c["metadata"]["heading"] for c in chunks if word in c["text"]]

    assert heading_of("Intro") == ["Introduction"]
    assert heading_of("Continuing") == ["Introduction"]
    assert heading_of("Findings") == ["Results"]


def test_ruled_table_becomes_one_markdown_chunk(tmp_path: Path) -> None:
    rows = [
        ["Region", "Revenue", "Growth"],
        ["Europe", "120", "-2.3%"],
        ["Asia", "340", "5.1%"],
        ["Africa", "45", "9.8%"],
    ]
    doc = fitz.open()
    page = _table_page(doc, rows, intro="Regional results are tabulated below.")
    assert len(page.find_tables().tables) == 1, "fixture must be a detectable table"
    path = _save(doc, tmp_path)

    ingestor, chunks = _ingest(path)

    tables = [c for c in chunks if c["metadata"]["kind"] == "table"]
    assert len(tables) == 1
    lines = [ln.strip() for ln in tables[0]["text"].splitlines() if ln.strip()]
    assert lines[0] == "| Region | Revenue | Growth |"
    assert re.fullmatch(r"\|\s*:?-{3,}:?\s*\|\s*:?-{3,}:?\s*\|\s*:?-{3,}:?\s*\|", lines[1])
    assert "| Europe | 120 | -2.3% |" in lines
    assert "&#45;" not in tables[0]["text"] and "-2.3%" in tables[0]["text"]
    assert ingestor.stats()["tables"] == 1


def test_table_caption_above_starts_the_table_chunk(tmp_path: Path) -> None:
    rows = [["A", "B", "C"], ["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"]]
    doc = fitz.open()
    _table_page(doc, rows, caption_above="Table 2: Quarterly revenue by region")
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path)

    table = next(c for c in chunks if c["metadata"]["kind"] == "table")
    assert table["text"].lstrip().startswith("Table 2: Quarterly revenue by region")
    # The caption belongs to the table, so the page's text chunks do not repeat it.
    assert not any("Table 2" in c["text"] for c in chunks if c["metadata"]["kind"] == "text")


def test_table_caption_below_also_starts_the_table_chunk(tmp_path: Path) -> None:
    rows = [["A", "B", "C"], ["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"]]
    doc = fitz.open()
    _table_page(doc, rows, caption_below="Table 3: Headcount by team")
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path)

    table = next(c for c in chunks if c["metadata"]["kind"] == "table")
    assert table["text"].lstrip().startswith("Table 3: Headcount by team")


def test_table_cell_text_is_not_repeated_in_text_chunks(tmp_path: Path) -> None:
    rows = [
        ["Region", "Revenue", "Growth"],
        ["Zanzibar", "120", "1.1%"],
        ["Patagonia", "340", "2.2%"],
        ["Lapland", "45", "3.3%"],
    ]
    doc = fitz.open()
    _table_page(doc, rows, intro="Regional results are tabulated below.")
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path)

    text_chunks = [c for c in chunks if c["metadata"]["kind"] == "text"]
    assert any("tabulated" in c["text"] for c in text_chunks)
    for place in ("Zanzibar", "Patagonia", "Lapland"):
        assert not any(place in c["text"] for c in text_chunks)
        assert any(place in c["text"] for c in chunks if c["metadata"]["kind"] == "table")


def test_every_chunk_of_a_table_page_says_has_tables(tmp_path: Path) -> None:
    rows = [["A", "B", "C"], ["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"]]
    doc = fitz.open()
    _table_page(doc, rows, intro="Some words above the table.")
    _text_page(doc, ["A plain page with nothing tabular."])
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path)

    first, second = _on_page(chunks, 0), _on_page(chunks, 1)
    assert len(first) >= 2
    assert all(c["metadata"]["has_tables"] is True for c in first)
    assert second and all(c["metadata"]["has_tables"] is False for c in second)
    assert all(c["metadata"]["page_kind"] == "visual" for c in first)


def test_page_with_a_large_image_is_visual_and_has_figures(tmp_path: Path) -> None:
    doc = fitz.open()
    _image_page(doc)
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path)

    assert chunks
    for c in chunks:
        assert c["metadata"]["page_kind"] == "visual"
        assert c["metadata"]["has_figures"] is True


def test_pure_text_page_is_text_without_figures(tmp_path: Path) -> None:
    doc = fitz.open()
    _text_page(doc, ["Nothing but words on this page."])
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path)

    assert chunks
    for c in chunks:
        assert c["metadata"]["page_kind"] == "text"
        assert c["metadata"]["has_figures"] is False
        assert c["metadata"]["has_tables"] is False


def test_scanned_page_is_ocred_once_and_its_lines_become_ocr_chunks(tmp_path: Path) -> None:
    doc = fitz.open()
    _text_page(doc, ["A page that has a real text layer."])
    page = _scanned_page(doc, ["Scanned words here."])
    assert page.get_text().strip() == "", "fixture must have no text layer"
    path = _save(doc, tmp_path)
    ocr = FakeOcr(["Recognised line one.", "Recognised line two."])

    _, chunks = _ingest(path, ocr=ocr)

    assert len(ocr.calls) == 1
    assert ocr.calls[0].startswith(b"\x89PNG")
    ocr_chunks = _on_page(chunks, 1)
    assert ocr_chunks
    for c in ocr_chunks:
        assert c["metadata"]["kind"] == "ocr"
        assert c["metadata"]["page_kind"] == "scanned"
    joined = _all_text(ocr_chunks)
    assert "Recognised line one." in joined and "Recognised line two." in joined
    assert all(c["metadata"]["kind"] == "text" for c in _on_page(chunks, 0))


def test_ocr_engine_is_not_called_for_pages_with_a_text_layer(tmp_path: Path) -> None:
    doc = fitz.open()
    _text_page(doc, ["First page of words."])
    _text_page(doc, ["Second page of words."])
    path = _save(doc, tmp_path)
    ocr = FakeOcr(["never used"])

    _, chunks = _ingest(path, ocr=ocr)

    assert ocr.calls == []
    assert chunks


def test_ocr_chunks_obey_the_size_limit(tmp_path: Path) -> None:
    doc = fitz.open()
    _scanned_page(doc, ["Scanned words here."])
    path = _save(doc, tmp_path)
    lines = [f"Recognised line number {i} of the long scan." for i in range(30)]

    _, chunks = _ingest(path, ocr=FakeOcr(lines), max_chunk_chars=200)

    assert len(chunks) > 1
    assert all(len(c["text"]) <= 200 and c["metadata"]["kind"] == "ocr" for c in chunks)
    assert _words(_all_text(chunks)) == _words(" ".join(lines))


def test_scanned_page_without_ocr_yields_nothing_but_the_rest_still_works(
    tmp_path: Path,
) -> None:
    doc = fitz.open()
    _text_page(doc, ["Readable first page."])
    _scanned_page(doc, ["Scanned words here."])
    _text_page(doc, ["Readable third page."])
    path = _save(doc, tmp_path)

    ingestor, chunks = _ingest(path, ocr=None)

    assert {c["page"] for c in chunks} == {0, 2}
    assert ingestor.stats()["scanned_pages"] == 1
    assert ingestor.stats()["ocr_pages"] == 0


def _mixed_document(tmp_path: Path) -> Path:
    doc = fitz.open()
    _text_page(doc, ["Plain text page."])
    _image_page(doc)
    _table_page(
        doc,
        [["A", "B", "C"], ["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"]],
        intro="Words above the table.",
    )
    _scanned_page(doc, ["Scanned words here."])
    return _save(doc, tmp_path, "mixed.pdf")


def test_page_layouts_has_an_entry_for_every_page_even_without_chunks(
    tmp_path: Path,
) -> None:
    path = _mixed_document(tmp_path)

    ingestor, chunks = _ingest(path, ocr=None)

    assert len(ingestor.page_layouts) == 4
    assert set(ingestor.page_layouts) == {0, 1, 2, 3}
    assert 3 not in {c["page"] for c in chunks}


def test_stats_count_a_mixed_document(tmp_path: Path) -> None:
    path = _mixed_document(tmp_path)

    ingestor, chunks = _ingest(path, ocr=FakeOcr(["Recognised scan text."]))

    stats = ingestor.stats()
    assert stats["pages"] == 4
    assert stats["text_pages"] == 1
    assert stats["visual_pages"] == 2
    assert stats["scanned_pages"] == 1
    assert stats["ocr_pages"] == 1
    assert stats["tables"] == 1
    assert stats["chunks"] == len(chunks)
    assert all(isinstance(v, int) for v in stats.values())


def test_every_chunk_has_a_bbox_of_four_floats_inside_its_page(tmp_path: Path) -> None:
    path = _mixed_document(tmp_path)

    _, chunks = _ingest(path, ocr=FakeOcr(["Recognised scan text."]))

    assert chunks
    tol = 1.0
    for c in chunks:
        x0, y0, x1, y1 = c["metadata"]["bbox"]
        assert all(isinstance(v, float) for v in (x0, y0, x1, y1))
        assert -tol <= x0 <= x1 <= PAGE_W + tol
        assert -tol <= y0 <= y1 <= PAGE_H + tol


@pytest.mark.parametrize(
    "key", ["kind", "heading", "page_kind", "has_tables", "has_figures", "bbox"]
)
def test_chunk_metadata_carries_every_documented_key(tmp_path: Path, key: str) -> None:
    doc = fitz.open()
    _text_page(doc, ["Some ordinary words."])
    path = _save(doc, tmp_path)

    _, chunks = _ingest(path)

    assert chunks and all(key in c["metadata"] for c in chunks)


def test_a_ruled_grid_whose_cells_are_mostly_empty_is_not_a_table(tmp_path: Path) -> None:
    """A figure drawn with lines — the Attention paper's attention maps — reads
    to `find_tables` as a grid with a word in one cell of every few. Quoted as
    a table it is a wall of `|  |  |`; as text, its words still say something."""
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    words = {(0, 0): "The", (2, 3): "Law", (4, 1): "will", (5, 5): "never"}
    for r in range(6):
        for c in range(6):
            rect = fitz.Rect(72 + c * 70, 200 + r * 22, 72 + (c + 1) * 70, 200 + (r + 1) * 22)
            page.draw_rect(rect, color=(0, 0, 0), width=0.6)
            if (r, c) in words:
                _write_html(page, rect + (3, 2, -3, -2), f'<p style="font-size:10pt">{words[(r, c)]}</p>')
    assert page.find_tables().tables, "fixture must look like a table to find_tables"
    path = _save(doc, tmp_path)

    ingestor, chunks = _ingest(path)

    assert not any(c["metadata"]["kind"] == "table" for c in chunks)
    assert ingestor.stats()["tables"] == 0
    assert "Law" in _all_text(chunks)
