"""Keyword scoring for chat retrieval: BM25 over one index's chunks (#31).

`vector_store.hybrid_search` blends these scores with semantic similarity. The
keyword side used to count which query words appeared anywhere in a chunk, as
substrings: "the" weighed as much as the one rare term a question hinged on,
"rate" matched inside "accurate", and the scan ran on the event loop.

An index is built from the chunk list a question has already read
(`vector_store.get_chunks`), so it only ever scores that one document, and it is
built once per question for both keyword passes. Building and scoring are CPU
work: call both through `asyncio.to_thread`.

Stdlib-only. `rank-bm25` was dropped from the dependencies and is excluded from
the bundle (`pdfusion-sidecar.spec`), and Okapi BM25 is a few lines.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from typing import Dict, List, Sequence, Tuple

# Okapi BM25's usual constants: how quickly repeating a term stops adding to a
# chunk's score, and how much a long chunk is discounted.
K1 = 1.5
B = 0.75

_WORD = re.compile(r"\w+")


def tokenize(text: str) -> List[str]:
    """The words of `text`, folded as the PDF viewer's find folds them
    (`desktop/src/lib/pdf-viewer/find.ts`).

    Case and diacritics are ignored, and `đ` reads as `d`, so a question typed
    without Vietnamese marks still matches text that has them.
    """
    if text.isascii():
        return _WORD.findall(text.lower())
    # Marks go before case folding, as in find.ts. `đ` is a letter of its own,
    # not `d` plus a mark, so decomposition leaves it alone.
    stripped = "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if not unicodedata.category(ch).startswith("M")
    )
    return _WORD.findall(stripped.casefold().replace("đ", "d"))


class Bm25Index:
    """BM25 statistics for one index's chunks, in the order they were given."""

    def __init__(self, texts: Sequence[str]) -> None:
        self._lengths: List[int] = []
        postings: Dict[str, List[Tuple[int, int]]] = {}
        for position, text in enumerate(texts):
            counts = Counter(tokenize(text))
            self._lengths.append(sum(counts.values()))
            for term, frequency in counts.items():
                postings.setdefault(term, []).append((position, frequency))
        self._postings = postings

        count = len(self._lengths)
        self._average_length = sum(self._lengths) / count if count else 0.0
        # Lucene's form of IDF, which is never negative: a term found in most
        # chunks still counts a little, rather than counting against them.
        self._idf = {
            term: math.log(1 + (count - len(hits) + 0.5) / (len(hits) + 0.5))
            for term, hits in postings.items()
        }

    def __len__(self) -> int:
        return len(self._lengths)

    def scores(self, query: str) -> List[float]:
        """Every chunk's score for `query`, in chunk order, scaled so the best
        is 1.0, like the similarities it is blended with. All zeros when no
        chunk has any of the query's words. A word repeated in the query counts
        once.
        """
        scores = [0.0] * len(self._lengths)
        for term in dict.fromkeys(tokenize(query)):
            hits = self._postings.get(term)
            if not hits:
                continue
            idf = self._idf[term]
            for position, frequency in hits:
                length_norm = 1 - B + B * self._lengths[position] / self._average_length
                scores[position] += idf * frequency * (K1 + 1) / (frequency + K1 * length_norm)
        best = max(scores, default=0.0)
        return [score / best for score in scores] if best > 0 else scores
