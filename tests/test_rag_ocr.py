"""`rag/ocr.py`: putting back the spaces RapidOCR's recogniser drops.

Its bundled model (PP-OCRv4, trained mostly on Chinese) reads an English line
as "TheBoardofDirectorsapproved", which no keyword or embedding search can
match. Each character comes with its box, though, and the gap between two
words is wider than the gap between two letters of one.
"""

from __future__ import annotations

from desktop_pdf_translator.rag.ocr import respace


def _boxes(spans):
    """Character boxes 36 px tall from (x0, x1) pairs, as RapidOCR returns
    them: four corners, clockwise from the top left."""
    return [[[x0, 0], [x1, 0], [x1, 36], [x0, 36]] for x0, x1 in spans]


def test_a_gap_wider_than_a_fifth_of_the_line_height_is_a_space():
    # T h e | B o a: letters touch or sit 3 px apart; the word gap is 9 px,
    # more than 0.2 x 36 = 7.2 px.
    chars = list("TheBoa")
    boxes = _boxes([(120, 135), (138, 151), (151, 165), (174, 189), (192, 205), (205, 217)])

    assert respace(chars, boxes) == "The Boa"


def test_a_gap_narrower_than_that_is_not():
    # 7 px < 7.2 px: still one word.
    chars = list("ab")
    boxes = _boxes([(0, 10), (17, 27)])

    assert respace(chars, boxes) == "ab"


def test_spaces_the_recogniser_did_emit_are_kept_and_not_doubled():
    chars = list("a b")
    boxes = _boxes([(0, 10), (10, 30), (30, 40)])

    assert respace(chars, boxes) == "a b"


def test_an_empty_line_is_empty():
    assert respace([], []) == ""
