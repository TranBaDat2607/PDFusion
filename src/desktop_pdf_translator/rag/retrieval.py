"""Fusing rankings and turning chunk hits into page evidence.

Stub: the signatures the tests are written against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Set, Tuple


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = 60) -> List[Tuple[str, float]]:
    raise NotImplementedError


@dataclass
class PageEvidence:
    page: int
    score: float
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    matched_ids: Set[str] = field(default_factory=set)
    page_kind: str = "text"
    wants_image: bool = False


def group_by_page(
    fused: Sequence[Tuple[str, float]],
    chunks: Sequence[Dict[str, Any]],
    max_pages: int,
    neighbours: int = 1,
) -> List[PageEvidence]:
    raise NotImplementedError


def select_image_pages(evidence: Sequence[PageEvidence], max_images: int) -> List[PageEvidence]:
    raise NotImplementedError


def chunk_map(chunks: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    raise NotImplementedError
