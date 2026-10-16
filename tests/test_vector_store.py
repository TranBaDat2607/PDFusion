"""`rag/vector_store.py`: what a chat index stores for each chunk."""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List

from chromadb import EmbeddingFunction

from desktop_pdf_translator.rag.vector_store import ChromaDBManager

_WORD = re.compile(r"\w+")


class _Hashing(EmbeddingFunction):
    """Bag of words, hashed into enough buckets that these tests' few words
    never share one: a collision would make unrelated texts look alike."""

    def __init__(self) -> None:
        pass

    @staticmethod
    def name() -> str:
        return "pdfusion-test-hashing"

    def default_space(self) -> str:
        return "cosine"

    def get_config(self) -> Dict[str, Any]:
        return {}

    @staticmethod
    def build_from_config(config: Dict[str, Any]) -> "_Hashing":
        return _Hashing()

    def __call__(self, input: List[str]) -> List[List[float]]:
        vectors = []
        for text in input:
            vector = [0.0] * 1024
            for word in _WORD.findall(text.lower()):
                vector[int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "little") % 1024] += 1.0
            vector[0] += 0.01  # never a zero vector
            vectors.append(vector)
        return vectors


def _chunk(text: str, heading: str, page: int = 0, **meta: Any) -> Dict[str, Any]:
    return {"text": text, "page": page, "metadata": {"heading": heading, **meta}}


def test_a_chunk_is_found_by_its_heading_though_its_text_never_says_it(tmp_path: Path):
    """A row of a product table doesn't say "product"; the heading it sits
    under does, so the heading is part of what is embedded. The other chunk
    shares one of the question's words in its own text, so it is the nearer of
    the two unless the heading counts."""
    store = ChromaDBManager(persist_directory=tmp_path / "v", embedding_function=_Hashing())
    asyncio.run(store.add_chunks("i", [
        _chunk("The catalogue is printed in two weeks.", heading="Logistics"),
        _chunk("Sensor S2 ships 84,000 units a year.", heading="Products catalogue"),
    ]))

    hits = asyncio.run(store.search_similar("i", "products catalogue", n_results=2))

    assert hits[0]["text"] == "Sensor S2 ships 84,000 units a year."


def test_the_text_stored_is_the_chunks_own_without_its_heading(tmp_path: Path):
    """The heading helps find the chunk; it is not quoted as the document's words."""
    store = ChromaDBManager(persist_directory=tmp_path / "v", embedding_function=_Hashing())
    asyncio.run(store.add_chunks("i", [_chunk("Body words.", heading="Methods")]))

    (chunk,) = asyncio.run(store.get_chunks("i"))

    assert chunk["text"] == "Body words."
    assert chunk["metadata"]["heading"] == "Methods"


def test_the_page_kind_and_chunk_kind_are_kept(tmp_path: Path):
    """The answer step picks which pages to show as images from these."""
    store = ChromaDBManager(persist_directory=tmp_path / "v", embedding_function=_Hashing())
    asyncio.run(store.add_chunks("i", [
        _chunk("| a | b |", heading="", page=3, kind="table", page_kind="visual", has_tables=True),
    ]))

    (chunk,) = asyncio.run(store.get_chunks("i"))

    assert chunk["metadata"]["page"] == 3
    assert chunk["metadata"]["kind"] == "table"
    assert chunk["metadata"]["page_kind"] == "visual"
    assert chunk["metadata"]["has_tables"] is True
