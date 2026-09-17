"""Which pages a translation covers, and whether it may run at all (#33).

A request names its pages as 1-indexed, inclusive `(first, last)` ranges, or
names none and means the whole document. `validate_selection` is the one
check both `POST /translate` and `PDFProcessor._validate_file` make, so the
pre-flight and the job can never disagree about what is allowed.

`max_pages` limits the pages a translation *covers*, not the pages a
document has: a 500-page book can be translated 50 pages at a time. The
pages outside the selection still appear in the output, as the original
pages, because the viewer needs a document as long as the source.

Standard library only. The pre-flight runs on the sidecar's boot path, and
the tests import this without PyMuPDF or BabelDOC.
"""

from __future__ import annotations

from typing import List, NamedTuple, Optional, Sequence, Tuple

PageRange = Tuple[int, int]

# Where the limits live in the app, named in every sentence that cites one.
LIMITS_LOCATION = "Settings → Cache → Performance"


class PageSelectionError(ValueError):
    """A selection or a document that can't be translated. The message is a
    sentence for the user."""


def _pages(count: int) -> str:
    return f"{count} page{'' if count == 1 else 's'}"


def selected_pages(
    ranges: Optional[Sequence[PageRange]], page_count: int
) -> Optional[List[int]]:
    """The pages `ranges` names, ascending and without repeats.

    `None` means the whole document, and is also what a selection covering
    every page comes back as: such a run is an ordinary full translation, and
    is cached as one.
    """
    if ranges is None:
        return None
    if not ranges:
        raise PageSelectionError("Choose at least one page to translate.")
    pages: set[int] = set()
    for first, last in ranges:
        if first < 1:
            raise PageSelectionError("Page numbers start at 1.")
        if first > last:
            raise PageSelectionError(
                f"The range {first}–{last} ends before it starts."
            )
        if last > page_count:
            raise PageSelectionError(
                f"This PDF has {_pages(page_count)}, so there is no page {last}."
            )
        pages.update(range(first, last + 1))
    if len(pages) == page_count:
        return None
    return sorted(pages)


def page_runs(pages: Sequence[int]) -> List[PageRange]:
    """Consecutive pages folded into ranges: `[1, 2, 3, 7]` → `[(1, 3), (7, 7)]`.
    `pages` must be ascending."""
    runs: List[PageRange] = []
    for page in pages:
        if runs and runs[-1][1] + 1 == page:
            runs[-1] = (runs[-1][0], page)
        else:
            runs.append((page, page))
    return runs


def format_pages(pages: Sequence[int]) -> str:
    """`"1–20, 35"` — for sentences."""
    return ", ".join(
        str(first) if first == last else f"{first}–{last}"
        for first, last in page_runs(pages)
    )


def pages_key(pages: Sequence[int]) -> str:
    """`"1-20,35"` — for the PDF cache key. ASCII and canonical: two requests
    naming the same pages differently get the same key."""
    return ",".join(
        str(first) if first == last else f"{first}-{last}"
        for first, last in page_runs(pages)
    )


def contiguous_runs(pages: Sequence[int], max_len: int) -> List[PageRange]:
    """The chunks a translation is split into: runs of consecutive pages, at
    most `max_len` long, in page order. `rolling_segments` relies on that
    order."""
    if max_len < 1:
        raise ValueError("max_len must be at least 1")
    chunks: List[PageRange] = []
    for first, last in page_runs(pages):
        for start in range(first, last + 1, max_len):
            chunks.append((start, min(start + max_len - 1, last)))
    return chunks


class Segment(NamedTuple):
    """A stretch of the rolling PDF: the translated output of chunk `chunk`,
    or — when `chunk` is `None` — pages `first`..`last` of the original."""

    chunk: Optional[int]
    first: int
    last: int


def rolling_segments(
    total_pages: int,
    chunk_ranges: Sequence[PageRange],
    done: Sequence[bool],
) -> List[Segment]:
    """How to assemble a full-length rolling PDF from the chunks finished so far.

    Every page that isn't in a finished chunk comes from the original, and
    neighbouring original pages are taken as one run. The rolling PDF is
    rebuilt after every chunk, so for a 500-page document with 20 pages
    selected, this is the difference between a few `insert_pdf` calls per
    rebuild and 500.
    """
    segments: List[Segment] = []

    def original(first: int, last: int) -> None:
        if first > last:
            return
        if segments and segments[-1].chunk is None and segments[-1].last + 1 == first:
            segments[-1] = Segment(None, segments[-1].first, last)
        else:
            segments.append(Segment(None, first, last))

    cursor = 1
    for idx in sorted(range(len(chunk_ranges)), key=lambda i: chunk_ranges[i][0]):
        first, last = chunk_ranges[idx]
        if first < cursor or last > total_pages:
            raise ValueError(f"chunk {idx} ({first}-{last}) overlaps or overruns")
        original(cursor, first - 1)
        if done[idx]:
            segments.append(Segment(idx, first, last))
        else:
            original(first, last)
        cursor = last + 1
    original(cursor, total_pages)
    return segments


def limit_problem(
    *,
    page_count: int,
    selected_count: int,
    size_mb: float,
    max_pages: int,
    max_size_mb: float,
) -> Optional[str]:
    """Why a translation of `selected_count` pages of this document may not
    run, or `None`."""
    if size_mb > max_size_mb:
        return (
            f"This PDF is {size_mb:.1f} MB, and PDFusion translates files up to "
            f"{max_size_mb:g} MB. You can raise the limit in {LIMITS_LOCATION}."
        )
    if selected_count > max_pages:
        if selected_count == page_count:
            return (
                f"This PDF has {page_count} pages, and PDFusion translates up to "
                f"{max_pages} at a time. Choose the pages to translate in the "
                f"toolbar, or raise the limit in {LIMITS_LOCATION}."
            )
        return (
            f"{selected_count} pages are selected, and PDFusion translates up to "
            f"{max_pages} at a time. Choose fewer pages, or raise the limit in "
            f"{LIMITS_LOCATION}."
        )
    return None


def validate_selection(
    ranges: Optional[Sequence[PageRange]],
    *,
    page_count: int,
    size_mb: float,
    max_pages: int,
    max_size_mb: float,
) -> Optional[List[int]]:
    """The pages to translate (`None`: all of them), or `PageSelectionError`
    with the reason they can't be."""
    pages = selected_pages(ranges, page_count)
    problem = limit_problem(
        page_count=page_count,
        selected_count=page_count if pages is None else len(pages),
        size_mb=size_mb,
        max_pages=max_pages,
        max_size_mb=max_size_mb,
    )
    if problem:
        raise PageSelectionError(problem)
    return pages
