"""`rag/citations.py`: page citations checked against the evidence shown.

The model is told to cite `[p. 7]`; whatever it writes, only pages it was
actually shown may become links, and anything else must not look like a source.
"""

from __future__ import annotations

import pytest

from desktop_pdf_translator.rag.citations import link_citations


def link(n):
    return f"[p. {n}](#page={n})"


def test_a_single_citation_becomes_a_link_and_is_recorded():
    result = link_citations("Water boils at 100 C [p. 7].", {7})
    assert result.text == f"Water boils at 100 C {link(7)}."
    assert result.cited_pages == [7]
    assert result.invalid_pages == []


def test_several_pages_in_one_marker_are_linked_separately():
    result = link_citations("Fact [p. 3, 5].", {3, 5})
    assert result.text == f"Fact {link(3)}, {link(5)}."
    assert result.cited_pages == [3, 5]


@pytest.mark.parametrize("dash", ["-", "–"])
def test_a_range_expands_to_the_allowed_pages_inside_it(dash):
    result = link_citations(f"Fact [pp. 3{dash}5].", {3, 5, 9})
    assert result.text == f"Fact {link(3)}, {link(5)}."
    assert result.cited_pages == [3, 5]


def test_a_fully_allowed_range_lists_every_page_ascending():
    result = link_citations("Fact [pp. 3-5].", {3, 4, 5})
    assert result.text == f"Fact {link(3)}, {link(4)}, {link(5)}."
    assert result.cited_pages == [3, 4, 5]


@pytest.mark.parametrize(
    "marker",
    [
        "[p. 7]",
        "[p 7]",
        "[P. 7]",
        "[pp. 7]",
        "[pp 7]",
        "[page 7]",
        "[Page 7]",
        "[pages 7]",
        "[trang 7]",
        "[Trang 7]",
    ],
)
def test_every_citation_prefix_is_recognised(marker):
    result = link_citations(f"Fact {marker}.", {7})
    assert result.text == f"Fact {link(7)}."
    assert result.cited_pages == [7]


def test_an_invalid_page_is_dropped_and_recorded_but_its_valid_neighbour_stays():
    result = link_citations("Fact [p. 3, 99].", {3})
    assert result.text == f"Fact {link(3)}."
    assert result.cited_pages == [3]
    assert result.invalid_pages == [99]


def test_a_marker_with_only_invalid_pages_is_removed_with_its_preceding_space():
    result = link_citations("fact [p. 99].", {3})
    assert result.text == "fact."
    assert result.cited_pages == []
    assert result.invalid_pages == [99]


def test_cited_pages_are_unique_and_in_order_of_first_citation():
    result = link_citations("A [p. 5]. B [p. 2]. C [p. 5].", {2, 5})
    assert result.text == f"A {link(5)}. B {link(2)}. C {link(5)}."
    assert result.cited_pages == [5, 2]


def test_invalid_pages_are_unique_and_in_order_of_first_appearance():
    result = link_citations("A [p. 9]. B [p. 8]. C [p. 9].", {1})
    assert result.invalid_pages == [9, 8]


@pytest.mark.parametrize(
    "text",
    [
        "See reference [1] for details.",
        r"The probability lies in $x \in [0, 1]$ always.",
        "As explained [see above] earlier.",
        "Read [the docs](https://example.com/p/3) first.",
    ],
)
def test_brackets_that_are_not_page_citations_are_untouched(text):
    result = link_citations(text, {1, 3})
    assert result.text == text
    assert result.cited_pages == []
    assert result.invalid_pages == []


def test_an_already_linked_citation_is_left_as_is_and_counted_once():
    text = f"Fact {link(3)} and again [p. 3]."
    result = link_citations(text, {3})
    assert result.text == f"Fact {link(3)} and again {link(3)}."
    assert result.cited_pages == [3]


def test_an_answer_without_markers_is_returned_unchanged():
    result = link_citations("Nothing to cite here.", {1, 2})
    assert result.text == "Nothing to cite here."
    assert result.cited_pages == []
    assert result.invalid_pages == []
