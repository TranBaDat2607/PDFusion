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
from desktop_pdf_translator.rag.retrieval import outline, outline_line
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


def test_each_section_once_with_the_page_it_starts_on_in_page_order():
    chunks = [
        chunk(2, "2 Related Work"), chunk(0, "Abstract"), chunk(0, "1 Introduction"),
        chunk(1, "1 Introduction"), chunk(4, "4 Experiments"), chunk(3, "4 Experiments"),
    ]

    assert outline(chunks, max_chars=1000) == [
        (1, "Abstract"), (1, "1 Introduction"), (3, "2 Related Work"), (4, "4 Experiments"),
    ]


def test_a_heading_that_starts_a_later_section_again_is_listed_again():
    """A PDF of several papers repeats "Abstract" and "References"; each is
    where one paper starts or ends, so dropping repeats blurs the papers into
    one."""
    chunks = [chunk(0, "Paper A"), chunk(0, "Abstract"), chunk(2, "References"),
              chunk(3, "Paper B"), chunk(3, "Abstract"), chunk(5, "References")]

    assert [heading for _page, heading in outline(chunks, max_chars=1000)] == [
        "Paper A", "Abstract", "References", "Paper B", "Abstract", "References",
    ]


def test_subsections_are_listed_under_their_section():
    """A paper's "4.1 GLUE", "4.2 SQuAD v1.1" are where its benchmarks are."""
    def sub(page, heading, subheading):
        return {"text": "w", "metadata": {"page": page, "heading": heading, "subheading": subheading}}

    chunks = [sub(4, "4 Experiments", ""), sub(4, "4 Experiments", "4.1 GLUE"),
              sub(5, "4 Experiments", "4.1 GLUE"), sub(5, "4 Experiments", "4.2 SQuAD v1.1"),
              sub(6, "5 Ablation Studies", "")]

    assert [heading for _page, heading in outline(chunks, max_chars=1000)] == [
        "4 Experiments", "4.1 GLUE", "4.2 SQuAD v1.1", "5 Ablation Studies",
    ]


def test_chunks_without_a_heading_or_metadata_are_skipped():
    chunks = [{"text": "no metadata"}, chunk(0, None), chunk(1, "  "), chunk(2, "Results")]

    assert outline(chunks, max_chars=1000) == [(3, "Results")]


def test_a_document_with_no_headings_has_no_outline():
    assert outline([chunk(0, ""), chunk(1, "")], max_chars=1000) == []


def test_a_long_outline_is_cut_at_its_end_within_its_budget():
    """Cut at the end, never thinned out: a short heading after the cut must
    not slip in, or the outline would skip sections without saying so."""
    long_heading = "x" * 50
    chunks = [chunk(page, f"Section {page} {long_heading}") for page in range(20)]
    chunks.append(chunk(20, "Short"))

    entries = outline(chunks, max_chars=300)

    assert 0 < len(entries) < 20
    assert entries[0] == (1, f"Section 0 {long_heading}")
    assert (21, "Short") not in entries
    # Within the budget as rendered in the prompt.
    assert sum(len(outline_line(page, heading)) + 1 for page, heading in entries) <= 300


def test_the_budget_counts_text_that_takes_a_token_a_character_as_longer():
    """3,000 characters of CJK headings would be ~3k tokens, not ~1k."""
    latin = outline([chunk(n, f"Section {n:02d}") for n in range(50)], max_chars=600)
    cjk = outline([chunk(n, f"節{n:02d}章節章節章") for n in range(50)], max_chars=600)

    assert len(cjk) < len(latin)


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
        assert outline_line(page, heading) in inside, heading
    # A contents page comes first: the outline before the first page's text.
    assert inside.index(outline_line(1, headings[0])) < inside.index("--- page")


def test_an_outline_line_does_not_read_like_a_citation():
    """`p. 4: Appendix` invites `[p. 4]`, which the citation check then drops."""
    assert not re.search(r"\bp\.\s*\d", outline_line(4, "Appendix D"))
    assert "4" in outline_line(4, "Appendix D")


def test_the_prompt_says_the_outline_is_for_structure_and_never_cited(tmp_path, store):
    translator = _Translator("ok")
    index, path, _headings = build_long(tmp_path, store)

    ask(make_chain(store, translator, full_document_chars=0), index, path, max_pdf_sources=1)

    prompt, system, _kwargs = translator.calls[0]
    instructions = (prompt[prompt.rindex("Question:"):] + (system or "")).lower()
    assert "outline" in instructions
    assert "never cite" in instructions or "not cite" in instructions


def test_a_retry_without_images_keeps_the_outline(tmp_path, store, monkeypatch):
    """A model that refuses the page image is asked again without it; that
    second prompt is built separately and must carry the outline too."""
    from desktop_pdf_translator.rag import retrieval as retrieval_module
    from test_rag_answers import _SdkError

    # Every retrieved page reads better as a picture, so the first request
    # carries an image and the refusal path runs.
    monkeypatch.setattr(retrieval_module, "wants_image", lambda evidence: True)
    translator = _Translator(_SdkError(400, "This model does not support image input"), "ok")
    index, path, headings = build_long(tmp_path, store)

    ask(make_chain(store, translator, full_document_chars=0), index, path, max_pdf_sources=1)

    assert len(translator.calls) == 2
    assert "images" in translator.calls[0][2] and "images" not in translator.calls[1][2]
    assert outline_line(2, headings[1]) in fenced(translator.calls[1][0])


def test_an_outline_budget_of_zero_turns_it_off(tmp_path, store):
    translator = _Translator("ok")
    index, path, _headings = build_long(tmp_path, store)

    ask(make_chain(store, translator, full_document_chars=0, outline_chars=0), index, path,
        max_pdf_sources=1)

    assert "outline" not in translator.calls[0][0].lower()
