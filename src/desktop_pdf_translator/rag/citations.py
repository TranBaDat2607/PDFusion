"""Page citations in an answer: checked against the evidence, made clickable.

Stub: the signatures the tests are written against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Collection, List


@dataclass
class CitationResult:
    text: str
    cited_pages: List[int] = field(default_factory=list)
    invalid_pages: List[int] = field(default_factory=list)


def link_citations(answer: str, allowed_pages: Collection[int]) -> CitationResult:
    raise NotImplementedError
