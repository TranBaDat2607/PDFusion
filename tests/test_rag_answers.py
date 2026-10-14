"""What the chat's answer chain sends the model and what it makes of the reply.

Retrieval picks *pages* of evidence. The model reads them grouped and labelled
by page, is shown the rendered image of the pages where text alone loses the
content (figures, scans, tables), and cites pages as `[p. N]`; the chain
checks those citations against the evidence before they reach the chat.

Real ChromaDB under `tmp_path`, real PDFs (PyMuPDF) so pages can be rendered,
and a deterministic embedding function in place of the ONNX model.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF
import pytest
from chromadb import EmbeddingFunction

from desktop_pdf_translator.config import AppSettings, TranslationService
from desktop_pdf_translator.rag import rag_chain as rag_chain_module
from desktop_pdf_translator.rag.errors import AnswerGenerationError
from desktop_pdf_translator.rag.rag_chain import AnswerModel, EnhancedRAGChain
from desktop_pdf_translator.rag.vector_store import ChromaDBManager
from desktop_pdf_translator.translators.base import PageImage

QUESTION = "How does the quokka zephyr calibration drift?"
DOCUMENT_ID = "doc-sha"

_DIMENSIONS = 64
_WORD = re.compile(r"\w+")


class HashingEmbeddingFunction(EmbeddingFunction):
    """Bag of words, hashed into a fixed number of buckets: deterministic, and
    texts sharing words land near each other."""

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
    def build_from_config(config: Dict[str, Any]) -> "HashingEmbeddingFunction":
        return HashingEmbeddingFunction()

    def __call__(self, input: List[str]) -> List[List[float]]:
        vectors = []
        for text in input:
            vector = [0.0] * _DIMENSIONS
            for word in _WORD.findall(text.lower()):
                digest = hashlib.sha256(word.encode("utf-8")).digest()
                vector[int.from_bytes(digest[:4], "little") % _DIMENSIONS] += 1.0
            if not any(vector):
                vector[0] = 1.0
            vectors.append(vector)
        return vectors


# Page 1 matches the question best, page 4 least: rank follows page number.
RANKED_TEXTS = [
    "quokka zephyr calibration drift quokka zephyr calibration drift",
    "quokka zephyr calibration filler words about nothing",
    "quokka zephyr something else entirely unrelated words",
    "quokka plus lots of unrelated filler words here and there",
]


class _SdkError(Exception):
    """A provider SDK's error: an HTTP status and a message."""

    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message or f"Error code: {status_code}")
        self.status_code = status_code


class _Translator:
    """An LLM backend's `generate`. `replies` are consumed one per call (the
    last repeats); an exception instance is raised instead of returned.
    `calls` keeps `(prompt, system, kwargs)` — kwargs shows whether `images`
    was passed at all."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: List[Tuple[str, Optional[str], Dict[str, Any]]] = []

    def generate(self, prompt: str, system: str = None, max_tokens: int = 1000, **kwargs):
        self.calls.append((prompt, system, kwargs))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, BaseException):
            raise reply
        return reply


class _ThreeArgTranslator:
    """A backend written before images existed: no `images` parameter."""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: List[Tuple[str, Optional[str]]] = []

    def generate(self, prompt: str, system: str = None, max_tokens: int = 1000):
        self.calls.append((prompt, system))
        return self.reply


@pytest.fixture
def store(tmp_path: Path) -> ChromaDBManager:
    return ChromaDBManager(
        persist_directory=tmp_path / "vectors",
        embedding_function=HashingEmbeddingFunction(),
    )


@pytest.fixture(autouse=True)
def no_key_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rag_chain_module, "get_settings", lambda: AppSettings())


def make_chain(store: ChromaDBManager, translator, service=TranslationService.OPENAI,
               model: str = "gpt-test", **options) -> EnhancedRAGChain:
    chain = EnhancedRAGChain(store, **options)
    answer_model = None if translator is None else AnswerModel(service, translator, model)
    chain._answer_model = lambda: answer_model
    return chain


def build_document(
    tmp_path: Path, store: ChromaDBManager, pages: List[Dict[str, Any]]
) -> Tuple[str, Path]:
    """A PDF with one page per entry and an index with one chunk per page.

    Entry: `text`, and optionally `page_kind` ("text"), `kind` ("text").
    Chunk page numbers are 0-indexed and match pages that exist in the PDF.
    """
    path = tmp_path / "quokka.pdf"
    doc = fitz.open()
    for entry in pages:
        page = doc.new_page()
        page.insert_htmlbox(page.rect + (36, 36, -36, -36), f"<p>{entry['text']}</p>")
    doc.save(path)
    doc.close()
    chunks = [
        {
            "text": entry["text"],
            "page": number,
            "metadata": {
                "kind": entry.get("kind", "text"),
                "heading": "",
                "page_kind": entry.get("page_kind", "text"),
                "has_tables": entry.get("kind") == "table",
                "has_figures": entry.get("page_kind") == "visual",
            },
        }
        for number, entry in enumerate(pages)
    ]
    asyncio.run(store.add_chunks("idx", chunks))
    return "idx", path


def ask(chain: EnhancedRAGChain, index_id: str, path: Path, **kwargs) -> Dict[str, Any]:
    return asyncio.run(
        chain.answer_question(
            question=QUESTION,
            index_id=index_id,
            document_id=DOCUMENT_ID,
            document_path=str(path),
            answer_lang=kwargs.pop("answer_lang", "en"),
            **kwargs,
        )
    )


def ranked(*page_kinds: str) -> List[Dict[str, Any]]:
    """The ranked texts, page `i` having `page_kinds[i]` (the rest "text")."""
    kinds = list(page_kinds) + ["text"] * (len(RANKED_TEXTS) - len(page_kinds))
    return [{"text": t, "page_kind": k} for t, k in zip(RANKED_TEXTS, kinds)]


def image_labels(kwargs: Dict[str, Any]) -> List[Optional[str]]:
    return [image.label for image in kwargs["images"]]


# ---------------------------------------------------------------------------
# how many model calls
# ---------------------------------------------------------------------------


def test_one_model_call_per_question_without_hyde(tmp_path, store):
    translator = _Translator("It drifts [p. 1].")
    index, path = build_document(tmp_path, store, ranked())

    ask(make_chain(store, translator, hyde=False), index, path)

    assert len(translator.calls) == 1


def test_hyde_adds_a_search_call_before_the_answer(tmp_path, store):
    """The HyDE text is a search query: no language instruction, and it comes
    first."""
    translator = _Translator("a hypothetical passage about drift", "It drifts [p. 1].")
    index, path = build_document(tmp_path, store, ranked())

    result = ask(make_chain(store, translator, hyde=True), index, path, answer_lang="ja")

    (hyde_prompt, hyde_system, _), (prompt, system, _) = translator.calls
    assert "Japanese" not in hyde_prompt + (hyde_system or "")
    assert "Japanese" in prompt + system
    assert "[p. 1]" in result["answer"]


# ---------------------------------------------------------------------------
# the evidence in the prompt
# ---------------------------------------------------------------------------


def test_the_prompt_labels_each_evidence_page_with_its_one_indexed_number(tmp_path, store):
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, ranked())

    ask(make_chain(store, translator), index, path)

    (prompt, _system, _), = translator.calls
    lowered = prompt.lower()
    assert "page 0" not in lowered
    for number, text in enumerate(RANKED_TEXTS[:3], start=1):
        header = lowered.find(f"page {number}")
        assert header != -1, f"no header for page {number}"
        assert 0 <= header < prompt.find(text), "the header comes before the page's text"


def test_the_prompt_asks_for_page_citations_in_brackets(tmp_path, store):
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, ranked())

    ask(make_chain(store, translator), index, path)

    (prompt, system, _), = translator.calls
    assert "[p. N]" in prompt + system


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------


def test_a_visual_page_is_rendered_and_sent_with_its_label(tmp_path, store):
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, ranked("visual"))

    result = ask(make_chain(store, translator), index, path)

    (_, _, kwargs), = translator.calls
    images = kwargs["images"]
    assert all(isinstance(image, PageImage) and image.data for image in images)
    assert image_labels(kwargs) == ["[Image of page 1]"]
    assert result["quality_metrics"]["images_sent"] == 1


def test_a_scanned_page_is_sent_as_an_image(tmp_path, store):
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, ranked("text", "scanned"))

    ask(make_chain(store, translator), index, path)

    (_, _, kwargs), = translator.calls
    assert image_labels(kwargs) == ["[Image of page 2]"]


def test_a_page_with_a_table_is_sent_as_an_image(tmp_path, store):
    translator = _Translator("ok")
    pages = ranked()
    pages[2]["kind"] = "table"
    index, path = build_document(tmp_path, store, pages)

    ask(make_chain(store, translator), index, path)

    (_, _, kwargs), = translator.calls
    assert image_labels(kwargs) == ["[Image of page 3]"]


def test_a_text_page_is_never_sent_as_an_image(tmp_path, store):
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, ranked("text", "visual"))

    ask(make_chain(store, translator), index, path)

    (_, _, kwargs), = translator.calls
    assert image_labels(kwargs) == ["[Image of page 2]"]


def test_at_most_max_images_are_sent_best_ranked_first(tmp_path, store):
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, ranked("visual", "visual", "visual", "visual"))

    ask(make_chain(store, translator, max_images=2), index, path)

    (_, _, kwargs), = translator.calls
    assert image_labels(kwargs) == ["[Image of page 1]", "[Image of page 2]"]


def test_max_images_zero_sends_none_and_omits_the_keyword(tmp_path, store):
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, ranked("visual", "visual"))

    result = ask(make_chain(store, translator, max_images=0), index, path)

    (_, _, kwargs), = translator.calls
    assert "images" not in kwargs
    assert result["quality_metrics"]["images_sent"] == 0


def test_a_provider_that_takes_no_images_gets_no_images_keyword(tmp_path, store):
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, ranked("visual"))
    chain = make_chain(
        store, translator, service=TranslationService("deepseek"), model="deepseek-chat"
    )

    ask(chain, index, path)

    (prompt, _system, kwargs), = translator.calls
    assert "images" not in kwargs
    assert "[p. N]" in prompt  # the page text still reaches it, cited by page


def test_with_no_page_to_show_the_keyword_is_omitted_for_a_three_argument_backend(
    tmp_path, store
):
    translator = _ThreeArgTranslator("It drifts [p. 1].")
    index, path = build_document(tmp_path, store, ranked())

    result = ask(make_chain(store, translator), index, path)

    assert len(translator.calls) == 1
    assert "[p. 1](#page=1)" in result["answer"]


# ---------------------------------------------------------------------------
# a model that refuses images
# ---------------------------------------------------------------------------


def test_a_refusal_of_images_is_retried_without_them_and_remembered(tmp_path, store):
    refusal = _SdkError(400, "Invalid request: image input is not supported by this model")
    translator = _Translator(refusal, "It drifts [p. 1].", "Still [p. 1].")
    index, path = build_document(tmp_path, store, ranked("visual"))
    chain = make_chain(store, translator)

    first = ask(chain, index, path)

    assert len(translator.calls) == 2
    assert "images" in translator.calls[0][2]
    assert "images" not in translator.calls[1][2]
    assert "[p. 1](#page=1)" in first["answer"]
    assert first["quality_metrics"]["images_sent"] == 0

    second = ask(chain, index, path)

    assert len(translator.calls) == 3, "one call for the second question"
    assert "images" not in translator.calls[2][2]
    assert second["quality_metrics"]["images_sent"] == 0


def test_a_rejected_key_with_images_is_not_retried(tmp_path, store):
    translator = _Translator(_SdkError(401))
    index, path = build_document(tmp_path, store, ranked("visual"))

    with pytest.raises(AnswerGenerationError) as failure:
        ask(make_chain(store, translator), index, path)

    assert str(failure.value).startswith("OpenAI rejected the API key")
    assert len(translator.calls) == 1
    assert "images" in translator.calls[0][2], "it failed while sending images"


# ---------------------------------------------------------------------------
# citations and references
# ---------------------------------------------------------------------------


def test_a_citation_of_an_evidence_page_becomes_a_link_and_one_of_another_page_goes(
    tmp_path, store
):
    translator = _Translator("It drifts [p. 2] and recalibrates [p. 9].")
    index, path = build_document(tmp_path, store, ranked())

    result = ask(make_chain(store, translator), index, path)

    assert "[p. 2](#page=2)" in result["answer"]
    assert "p. 9" not in result["answer"]
    assert "page=9" not in result["answer"]


def test_references_are_one_per_page_cited_first_in_order_of_citation(tmp_path, store):
    translator = _Translator("First [p. 3]. Then [p. 1]. Again [p. 3].")
    index, path = build_document(tmp_path, store, ranked())

    result = ask(make_chain(store, translator), index, path)

    references = result["pdf_references"]
    assert [r["page"] for r in references] == [3, 1, 2, 4]
    assert [r["cited"] for r in references] == [True, True, False, False]
    assert {(r["document_id"], r["document_path"]) for r in references} == {
        (DOCUMENT_ID, str(path))
    }
    assert "quokka" in references[0]["text"]


def test_a_chunk_on_page_zero_is_page_one_in_the_references(tmp_path, store):
    translator = _Translator("ok")
    index, path = build_document(tmp_path, store, ranked())

    result = ask(make_chain(store, translator), index, path)

    references = result["pdf_references"]
    assert all("cited" in r for r in references)
    pages = [r["page"] for r in references]
    assert 0 not in pages and 1 in pages
    assert len(pages) == len(set(pages))


# ---------------------------------------------------------------------------
# quality metrics
# ---------------------------------------------------------------------------


def test_quality_metrics_count_images_cited_pages_and_invalid_citations(tmp_path, store):
    translator = _Translator("Drift [p. 1], again [p. 1], and [p. 9] and [p. 9] and [p. 12].")
    index, path = build_document(tmp_path, store, ranked("visual", "visual"))

    metrics = ask(make_chain(store, translator), index, path)["quality_metrics"]

    assert metrics["images_sent"] == 2
    assert metrics["pages_cited"] == 1
    assert metrics["invalid_citations"] == 2


# ---------------------------------------------------------------------------
# no model
# ---------------------------------------------------------------------------


def test_without_a_model_the_answer_quotes_the_best_evidence_with_page_links(tmp_path, store):
    index, path = build_document(tmp_path, store, ranked())

    result = ask(make_chain(store, None), index, path)

    assert "[p. 1](#page=1)" in result["answer"]
    linked = {int(n) for n in re.findall(r"\(#page=(\d+)\)", result["answer"])}
    cited = {r["page"] for r in result["pdf_references"] if r["cited"]}
    assert linked and linked == cited
    assert (result["provider"], result["model"]) == (None, None)
