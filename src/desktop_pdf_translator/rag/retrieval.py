"""Fusing rankings and turning chunk hits into page evidence.

Chat retrieval runs a semantic and a keyword search over one document's chunks
(`rag_chain.EnhancedRAGChain.retrieve`) and fuses them here. The two used to be
blended by score — cosine similarity plus BM25 scaled so its best hit was 1.0 —
which mixed two scales that mean nothing to each other: a chunk that matched
one rare word outranked every semantic hit, whatever the question. Reciprocal
rank fusion uses only each list's order, so neither side's scale matters.

The fused hits are then grouped by page. A page is what a citation names, what
the viewer jumps to, and what the answer model can be shown as an image, so it
is the unit of evidence: every hit on a page, with its neighbours on that page
for context, under one page number.

Pure functions over the chunk dicts `vector_store.get_chunks` returns.
Stdlib-only.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Set, Tuple

# The usual RRF constant (Cormack et al., 2009). Larger flattens the
# difference between a first and a tenth place.
RRF_K = 60

# Page kinds better read from a picture of the page than from its text layer:
# a scan has no text layer of its own, and on a "visual" page the chart or
# figure carries what the text doesn't (`ingest.PdfIngestor`).
_IMAGE_PAGE_KINDS = frozenset({"scanned", "visual"})


def contextual_text(chunk: Dict[str, Any]) -> str:
    """What retrieval embeds and keyword-scores for a chunk: its section
    heading, then its text (`ingest.PdfIngestor` keeps the heading in the
    metadata). The text alone is what the model and the reader see."""
    heading = chunk.get("metadata", {}).get("heading") or ""
    return f"{heading}\n{chunk['text']}" if heading else chunk["text"]


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> List[Tuple[str, float]]:
    """Every id in `rankings`, scored Σ 1/(k + rank) over the lists it is in.

    Best first; a tie keeps the order the ids were first seen in. An id listed
    twice in one ranking counts once, at its better place.
    """
    scores: Dict[str, float] = {}
    for ranking in rankings:
        seen: Set[str] = set()
        for rank, item in enumerate(ranking, start=1):
            if item in seen:
                continue
            seen.add(item)
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    # `sorted` is stable and `scores` keeps insertion order: first seen wins ties.
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


@dataclass
class PageEvidence:
    """One page's share of the evidence for a question."""

    page: int  # 0-indexed, as chunk metadata stores it
    score: float  # its best hit's fused score
    # Its hits and their neighbours on this page, in reading order.
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    matched_ids: Set[str] = field(default_factory=set)
    page_kind: str = "text"
    # Set by `select_image_pages`: the answer model is shown this page.
    wants_image: bool = False


def chunk_map(chunks: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {chunk["chunk_id"]: chunk for chunk in chunks}


def _index(chunk: Dict[str, Any]) -> int:
    return chunk.get("metadata", {}).get("chunk_index", 0)


def _page(chunk: Dict[str, Any]) -> int:
    return chunk.get("metadata", {}).get("page", 0)


def group_by_page(
    fused: Sequence[Tuple[str, float]],
    chunks: Sequence[Dict[str, Any]],
    max_pages: int,
    neighbours: int = 1,
) -> List[PageEvidence]:
    """The `max_pages` best pages among the fused hits.

    A page ranks by its best hit, not by how many it has: a long page matches
    a question's common words many times over, which says little. A tie goes
    to the page whose hits add up to more, then to the earlier page. Hits on
    chunks not in `chunks` are ignored.

    Each page brings its hits and up to `neighbours` chunks either side of
    each, never across the page boundary: a neighbour from the next page would
    be quoted under this page's number.
    """
    by_id = chunk_map(chunks)
    page_chunks: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        page_chunks[_page(chunk)].append(chunk)
    for members in page_chunks.values():
        members.sort(key=_index)

    hits: Dict[int, List[Tuple[str, float]]] = defaultdict(list)
    for chunk_id, score in fused:
        chunk = by_id.get(chunk_id)
        if chunk is not None:
            hits[_page(chunk)].append((chunk_id, score))

    ranked = sorted(
        hits.items(),
        key=lambda item: (
            -max(score for _, score in item[1]),
            -sum(score for _, score in item[1]),
            item[0],
        ),
    )

    evidence: List[PageEvidence] = []
    for page, page_hits in ranked[:max_pages]:
        members = page_chunks[page]
        position = {chunk["chunk_id"]: i for i, chunk in enumerate(members)}
        shown: Set[int] = set()
        for chunk_id, _ in page_hits:
            at = position[chunk_id]
            shown.update(range(max(0, at - neighbours), min(len(members), at + neighbours + 1)))
        evidence.append(
            PageEvidence(
                page=page,
                score=max(score for _, score in page_hits),
                chunks=[members[i] for i in sorted(shown)],
                matched_ids={chunk_id for chunk_id, _ in page_hits},
                page_kind=members[0].get("metadata", {}).get("page_kind", "text"),
            )
        )
    return evidence


def wants_image(evidence: PageEvidence) -> bool:
    """Whether the page reads better as a picture than as its text layer."""
    if evidence.page_kind in _IMAGE_PAGE_KINDS:
        return True
    return any(chunk.get("metadata", {}).get("kind") == "table" for chunk in evidence.chunks)


def select_image_pages(evidence: Sequence[PageEvidence], max_images: int) -> List[PageEvidence]:
    """Mark the best-ranked pages worth showing as images, at most `max_images`.

    Capped because each image costs the answer model roughly a page's worth
    of tokens, and only the best-ranked pages are likely to hold the answer.
    """
    picked: List[PageEvidence] = []
    for page in evidence:
        if len(picked) >= max_images:
            break
        if wants_image(page):
            page.wants_image = True
            picked.append(page)
    return picked
