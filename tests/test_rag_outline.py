"""A document's outline in the answer prompt (`retrieval.outline`).

A PDF too long to read whole is answered from the five pages retrieval picks,
which can't say what the rest of it holds: "what papers does this PDF
contain?" or "list the sections" got an answer from whichever five pages
matched. The headings ingest already keeps on every chunk are the document's
table of contents; given to the answer with their pages, they cover the rest
for a few hundred tokens and no model call.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

import pytest

from desktop_pdf_translator.config import AppSettings
from desktop_pdf_translator.rag import rag_chain as rag_chain_module
from desktop_pdf_translator.rag.retrieval import outline
from desktop_pdf_translator.rag.vector_store import ChromaDBManager

from test_rag_answers import (
    _FENCE,
    HashingEmbeddingFunction,
    _Translator,
    ask,
    make_chain,
)


@pytest.fixture
def store(tmp_path: Path) -> ChromaDBManager:
    return ChromaDBManager(
        persist_directory=tmp_path / "vectors",
        embedding_function=HashingEmbeddingFunction(),
    )


@pytest.fixture(autouse=True)
def no_key_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rag_chain_module, "get_settings", lambda: AppSettings())


def chunk(page: int, heading: str, text: str = "words") -> Dict[str, Any]:
    return {"text": text, "metadata": {"page": page, "heading": heading}}


# ---------------------------------------------------------------------------
# the outline itself
# ---------------------------------------------------------------------------


def test_each_heading_once_with_the_page_it_starts_on_in_page_order():
    chunks = [
        chunk(2, "2 Related Work"), chunk(0, "Abstract"), chunk(0, "1 Introduction"),
        chunk(1, "1 Introduction"), chunk(4, "4 Experiments"), chunk(3, "4 Experiments"),
    ]

    assert outline(chunks, max_chars=1000) == [
        (1, "Abstract"), (1, "1 Introduction"), (3, "2 Related Work"), (4, "4 Experiments"),
    ]


def test_a_document_with_no_headings_has_no_outline():
    assert outline([chunk(0, ""), chunk(1, "")], max_chars=1000) == []


def test_a_long_outline_is_cut_at_its_budget():
    chunks = [chunk(page, f"Section {page} " + "x" * 40) for page in range(200)]

    entries = outline(chunks, max_chars=600)

    assert 0 < len(entries) < 200
    assert sum(len(heading) + 8 for _page, heading in entries) <= 600
    # Cut at the end, never thinned out: the first sections stay.
    assert entries[0] == (1, "Section 0 " + "x" * 40)


# ---------------------------------------------------------------------------
# in the prompt
# ---------------------------------------------------------------------------


def build_long(tmp_path: Path, store: ChromaDBManager) -> tuple:
    """Eight pages, each under its own heading; only the first matches the
    question's words, so retrieval with max_pdf_sources=1 shows it alone."""
    import asyncio

    import fitz

    texts = ["quokka zephyr calibration drift"] + [f"filler page {n} lorem ipsum" for n in range(2, 9)]
    headings = ["1 Calibration"] + [f"{n} Appendix {chr(64 + n)}" for n in range(2, 9)]
    path = tmp_path / "long.pdf"
    doc = fitz.open()
    for text in texts:
        doc.new_page().insert_htmlbox(fitz.Rect(36, 36, 500, 700), f"<p>{text}</p>")
    doc.save(path)
    chunks = [
        {"text": text, "page": n, "metadata": {"kind": "text", "heading": heading, "page_kind": "text"}}
        for n, (text, heading) in enumerate(zip(texts, headings))
    ]
    asyncio.run(store.add_chunks("idx", chunks))
    return "idx", path, headings


def fenced(prompt: str) -> str:
    ((_tag, inside),) = _FENCE.findall(prompt)
    return inside


def test_a_document_answered_from_retrieved_pages_shows_its_outline_inside_the_fence(tmp_path, store):
    """Inside: headings are the document's words, as untrusted as its pages."""
    translator = _Translator("ok")
    index, path, headings = build_long(tmp_path, store)

    ask(make_chain(store, translator, full_document_chars=0), index, path, max_pdf_sources=1)

    inside = fenced(translator.calls[0][0])
    for page, heading in enumerate(headings, start=1):
        assert re.search(rf"p\. {page}\b[^\n]*{re.escape(heading)}", inside), heading


def test_a_document_read_whole_needs_no_outline(tmp_path, store):
    translator = _Translator("ok")
    index, path, headings = build_long(tmp_path, store)

    ask(make_chain(store, translator, full_document_chars=100_000), index, path, max_pdf_sources=1)

    assert "outline" not in translator.calls[0][0].lower()


def test_a_page_known_only_from_the_outline_cannot_be_cited(tmp_path, store):
    """The model saw a heading there, not the page: citing it would vouch for
    text it never read."""
    translator = _Translator("Appendix D is on [p. 4].")
    index, path, _headings = build_long(tmp_path, store)

    result = ask(make_chain(store, translator, full_document_chars=0), index, path, max_pdf_sources=1)

    assert "[p. 4]" not in result["answer"]
    assert result["quality_metrics"]["invalid_citations"] == 1
