"""`rag/page_images.py`: the pages the answer model is shown."""

from __future__ import annotations

from pathlib import Path

import fitz

from desktop_pdf_translator.rag.page_images import render_pages


def _pdf(tmp_path: Path, pages: int = 3) -> Path:
    doc = fitz.open()
    for n in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_htmlbox(fitz.Rect(50, 50, 545, 200), f"<p>Page body {n + 1}.</p>")
    path = tmp_path / "doc.pdf"
    doc.save(path)
    return path


def test_each_page_is_a_jpeg_labelled_with_its_one_indexed_number(tmp_path: Path):
    images = render_pages(_pdf(tmp_path), [2, 0])

    assert [image.label for image in images] == ["[Image of page 3]", "[Image of page 1]"]
    assert all(image.mime_type == "image/jpeg" and image.data[:2] == b"\xff\xd8" for image in images)


def test_the_long_side_is_capped(tmp_path: Path):
    """Providers bill an image by its size, and past ~1600 px they downscale it anyway."""
    (image,) = render_pages(_pdf(tmp_path), [0], max_side=800)

    pix = fitz.Pixmap(image.data)
    assert max(pix.width, pix.height) == 800


def test_a_missing_file_or_page_gives_no_image_rather_than_an_error(tmp_path: Path):
    """The answer can still be written from the text: a PDF moved since it was
    indexed costs the pictures, not the answer."""
    assert render_pages(tmp_path / "gone.pdf", [0]) == []
    assert len(render_pages(_pdf(tmp_path, pages=1), [0, 7])) == 1
