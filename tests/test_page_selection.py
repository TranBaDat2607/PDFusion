"""Which pages a translation covers, and whether it may run (#33).

`processors/page_selection.py` is the check both `POST /translate` and the
processor make, and the plan the processor splits and reassembles a document
by. All of it is plain arithmetic, so none of this needs a PDF.
"""

from __future__ import annotations

import pytest

from desktop_pdf_translator.processors.page_selection import (
    PageSelectionError,
    Segment,
    contiguous_runs,
    format_pages,
    limit_problem,
    page_runs,
    pages_key,
    rolling_segments,
    selected_pages,
    validate_selection,
)


# ---------------------------------------------------------------------------
# selected_pages
# ---------------------------------------------------------------------------


def test_no_ranges_is_the_whole_document():
    assert selected_pages(None, 10) is None


def test_ranges_become_sorted_unique_pages():
    assert selected_pages([(7, 8), (2, 3), (3, 4)], 10) == [2, 3, 4, 7, 8]


def test_a_selection_of_every_page_is_the_whole_document():
    """So it is cached, and reported, as an ordinary full translation."""
    assert selected_pages([(1, 4), (5, 10)], 10) is None


def test_a_single_page_document_selected_whole_is_the_whole_document():
    assert selected_pages([(1, 1)], 1) is None


@pytest.mark.parametrize(
    "ranges, message",
    [
        ([], "Choose at least one page to translate."),
        ([(0, 3)], "Page numbers start at 1."),
        ([(5, 2)], "The range 5–2 ends before it starts."),
        ([(9, 12)], "This PDF has 10 pages, so there is no page 12."),
    ],
)
def test_an_impossible_selection_says_why(ranges, message):
    with pytest.raises(PageSelectionError) as excinfo:
        selected_pages(ranges, 10)
    assert str(excinfo.value) == message


def test_a_one_page_document_is_not_called_pages():
    with pytest.raises(PageSelectionError, match="has 1 page, so"):
        selected_pages([(2, 2)], 1)


def test_a_page_selection_error_is_a_value_error():
    assert issubclass(PageSelectionError, ValueError)


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def test_consecutive_pages_fold_into_runs():
    assert page_runs([1, 2, 3, 7, 9, 10]) == [(1, 3), (7, 7), (9, 10)]
    assert page_runs([]) == []


def test_the_sentence_label_uses_an_en_dash():
    assert format_pages([1, 2, 3, 20, 35, 36]) == "1–3, 20, 35–36"


def test_the_cache_label_is_ascii_and_canonical():
    assert pages_key([1, 2, 3, 20]) == "1-3,20"
    # The same pages, however they were asked for, are the same key.
    assert pages_key(selected_pages([(2, 3), (1, 1), (20, 20)], 30)) == "1-3,20"


# ---------------------------------------------------------------------------
# contiguous_runs
# ---------------------------------------------------------------------------


def test_one_page_chunks_cover_exactly_the_selection():
    assert contiguous_runs([2, 3, 7], 1) == [(2, 2), (3, 3), (7, 7)]


def test_longer_chunks_never_cross_a_gap():
    assert contiguous_runs([1, 2, 3, 4, 5, 9, 10], 3) == [(1, 3), (4, 5), (9, 10)]


def test_the_whole_document_chunks_as_before():
    assert contiguous_runs(range(1, 8), 3) == [(1, 3), (4, 6), (7, 7)]


def test_a_chunk_has_at_least_one_page():
    with pytest.raises(ValueError):
        contiguous_runs([1], 0)


# ---------------------------------------------------------------------------
# rolling_segments
# ---------------------------------------------------------------------------


def test_nothing_done_is_the_original_in_one_piece():
    """Pending chunks and unselected pages alike come from the original, and
    neighbouring original pages are one `insert_pdf`, not one per page."""
    chunks = [(3, 3), (4, 4), (8, 8)]
    assert rolling_segments(10, chunks, [False, False, False]) == [
        Segment(None, 1, 10)
    ]


def test_finished_chunks_split_the_original_around_them():
    chunks = [(3, 3), (4, 4), (8, 8)]
    assert rolling_segments(10, chunks, [True, False, True]) == [
        Segment(None, 1, 2),
        Segment(0, 3, 3),
        Segment(None, 4, 7),
        Segment(2, 8, 8),
        Segment(None, 9, 10),
    ]


def test_every_page_translated_is_every_chunk_in_order():
    chunks = [(1, 1), (2, 2), (3, 3)]
    assert rolling_segments(3, chunks, [True, True, True]) == [
        Segment(0, 1, 1),
        Segment(1, 2, 2),
        Segment(2, 3, 3),
    ]


def test_segments_follow_page_order_whatever_the_chunk_order():
    chunks = [(5, 6), (1, 2)]
    assert rolling_segments(6, chunks, [True, True]) == [
        Segment(1, 1, 2),
        Segment(None, 3, 4),
        Segment(0, 5, 6),
    ]


def test_segments_cover_every_page_exactly_once():
    chunks = contiguous_runs([2, 3, 4, 9, 10, 15], 2)
    done = [i % 2 == 0 for i in range(len(chunks))]
    covered = [
        page
        for segment in rolling_segments(20, chunks, done)
        for page in range(segment.first, segment.last + 1)
    ]
    assert covered == list(range(1, 21))


@pytest.mark.parametrize("chunks", [[(1, 3), (3, 4)], [(9, 11)]])
def test_overlapping_or_overrunning_chunks_are_a_bug(chunks):
    with pytest.raises(ValueError):
        rolling_segments(10, chunks, [False] * len(chunks))


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def _problem(**overrides):
    values = dict(
        page_count=120, selected_count=120, size_mb=4.0, max_pages=50, max_size_mb=50.0
    )
    values.update(overrides)
    return limit_problem(**values)


def test_within_both_limits_there_is_no_problem():
    assert _problem(page_count=40, selected_count=40) is None
    assert _problem(selected_count=50) is None


def test_a_long_document_is_told_how_to_proceed():
    assert _problem() == (
        "This PDF has 120 pages, and PDFusion translates up to 50 at a time. "
        "Choose the pages to translate in the toolbar, or raise the limit in "
        "Settings → Cache → Performance."
    )


def test_a_selection_over_the_limit_is_told_to_choose_fewer():
    assert _problem(selected_count=60) == (
        "60 pages are selected, and PDFusion translates up to 50 at a time. "
        "Choose fewer pages, or raise the limit in Settings → Cache → Performance."
    )


def test_the_size_limit_applies_whatever_the_selection():
    assert _problem(selected_count=1, size_mb=72.25) == (
        "This PDF is 72.2 MB, and PDFusion translates files up to 50 MB. "
        "You can raise the limit in Settings → Cache → Performance."
    )


def test_validate_selection_returns_the_pages():
    assert (
        validate_selection(
            [(1, 50)], page_count=120, size_mb=4.0, max_pages=50, max_size_mb=50.0
        )
        == list(range(1, 51))
    )


def test_validate_selection_raises_the_limit_sentence():
    with pytest.raises(PageSelectionError, match="This PDF has 120 pages"):
        validate_selection(
            None, page_count=120, size_mb=4.0, max_pages=50, max_size_mb=50.0
        )


def test_validate_selection_reports_a_bad_range_before_the_limit():
    with pytest.raises(PageSelectionError, match="no page 200"):
        validate_selection(
            [(1, 200)], page_count=120, size_mb=4.0, max_pages=50, max_size_mb=50.0
        )
