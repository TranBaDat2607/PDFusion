"""Page citations in an answer: checked against the evidence, made clickable.

The answer model is shown numbered pages and told to cite them as `[p. 7]`.
What it writes is not trusted: a page it was not shown — misremembered, or
invented — must not reach the chat looking like a source. So every marker is
checked against the pages of the evidence, the ones that pass become
`[p. 7](#page=7)` links the chat panel turns into a jump to that page
(`desktop/src/lib/citation-links.ts`), and the rest are dropped and counted.

Only brackets that start with a page word are markers. A bare `[1]` is left
alone: it is how papers cite their bibliography, and `[0, 1]` is an interval.

Stdlib-only, and pure: `rag_chain` calls it on every answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Collection, List

# `[p. 7]`, `[pp. 3-5]`, `[page 2, 4]`, `[trang 7]` (Vietnamese). The prefix
# must be a whole word: `[pages]` alone, or `[part 2]`, is not a citation.
_MARKER = re.compile(
    r"(?P<space> ?)\[(?P<prefix>pp?\.?|pages?|trang)\s*"
    r"(?P<body>\d+(?:\s*[-–]\s*\d+)?(?:\s*,?\s*\d+(?:\s*[-–]\s*\d+)?)*)\]"
    r"(?P<linked>\(#page=\d+\))?",
    re.IGNORECASE,
)
_ITEM = re.compile(r"(\d+)(?:\s*[-–]\s*(\d+))?")

# A range wider than this is a typo or a hallucination, not a citation; it is
# read as its two ends rather than expanded into hundreds of links.
_MAX_RANGE = 20


@dataclass
class CitationResult:
    text: str
    # 1-indexed, unique, in the order first cited.
    cited_pages: List[int] = field(default_factory=list)
    # Pages a marker named that the model was not shown, in order first seen.
    invalid_pages: List[int] = field(default_factory=list)


def page_link(page: int) -> str:
    return f"[p. {page}](#page={page})"


def link_citations(answer: str, allowed_pages: Collection[int]) -> CitationResult:
    allowed = set(allowed_pages)
    cited: List[int] = []
    invalid: List[int] = []

    def note(page: int, into: List[int]) -> None:
        if page not in into:
            into.append(page)

    def replace(match: re.Match) -> str:
        if match.group("linked"):
            # Already a link: `link_citations` ran on this text before, or the
            # model copied the format. Counted, never linked twice.
            for page in _pages(match.group("body")):
                if page in allowed:
                    note(page, cited)
            return match.group(0)

        kept: List[int] = []
        for item in _ITEM.finditer(match.group("body")):
            start = int(item.group(1))
            end = int(item.group(2)) if item.group(2) else start
            if end < start or end - start > _MAX_RANGE:
                named = [start, end] if item.group(2) else [start]
            else:
                named = list(range(start, end + 1))
            for page in named:
                if page in allowed:
                    if page not in kept:
                        kept.append(page)
                elif page in (start, end):
                    # Only the pages a marker spells out count as invalid; the
                    # inside of a range is the model's shorthand, not a claim
                    # about each page in it.
                    note(page, invalid)
        if not kept:
            return ""
        for page in kept:
            note(page, cited)
        return match.group("space") + ", ".join(page_link(p) for p in kept)

    return CitationResult(_MARKER.sub(replace, answer), cited, invalid)


def _pages(body: str) -> List[int]:
    pages: List[int] = []
    for item in _ITEM.finditer(body):
        start = int(item.group(1))
        end = int(item.group(2)) if item.group(2) else start
        pages.extend(range(start, min(end, start + _MAX_RANGE) + 1))
    return pages
