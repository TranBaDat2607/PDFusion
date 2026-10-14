"""
The chat's answer chain: one question, one document's index, one answer that
cites its pages.

Retrieve by text, read by vision. Retrieval is local and costs nothing: a
semantic and a BM25 search over the document's chunks, fused by rank
(`retrieval.reciprocal_rank_fusion`) and grouped into pages
(`retrieval.group_by_page`). The answer model then reads those pages — their
text, under their page numbers, and, for the best-ranked pages whose content
the text layer loses (charts, figures, tables, scans), a rendered image of the
page (`retrieval.select_image_pages`, `page_images.render_pages`). It cites
pages as `[p. N]`, and `citations.link_citations` keeps only citations of pages
it was shown, as links the chat jumps from.

What this replaced, and why (measured in `benchmarks/rag/`): a score blend
that let one rare keyword outrank every semantic hit; a re-rank that scored
pages by how early in the document they were; HyDE on every question, a model
round trip before retrieval could start (`hyde=True` keeps it — see the
benchmark for what it bought); answers that cited nothing, with every
retrieved chunk listed as a reference whether the answer used it or not; and
no way to read a chart or a scan. DocPixie (github.com/qnguyen3/docpixie)
answers by showing the model every page of the document for every question;
this shows it at most `max_images` pages, chosen by retrieval, so a question
costs about the same on a 300-page document as on a 3-page one.
"""

import asyncio
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from ..config import ModelRef, TranslationService, get_settings
from ..providers.registry import llm_ids_by_priority, provider
from ..translators.base import (
    LANGUAGE_DISPLAY_NAMES,
    BaseTranslator,
    PageImage,
    is_fatal_translation_error,
)
from ..translators.capabilities import SERVICE_LABELS
from ..translators.factory import TranslatorFactory
from .citations import link_citations, page_link
from .errors import AnswerGenerationError, IndexUnavailableError
from .keyword_search import Bm25Index
from .page_images import render_pages
from .retrieval import (
    PageEvidence,
    chunk_map,
    contextual_text,
    group_by_page,
    reciprocal_rank_fusion,
    select_image_pages,
)
from .vector_store import ChromaDBManager

logger = logging.getLogger(__name__)

def _fallback_services() -> Tuple[TranslationService, ...]:
    """Chat's "any LLM with a key", in `ProviderSpec.priority` order. Answer
    synthesis needs an instruction-following model, so Argos is never one of
    them; nor is a keyless local server, which has no priority: chosen, it
    answers, but unchosen it is never tried, since it may not be running (#88).
    Read per question, from the registry as `provider()` sees it."""
    return tuple(TranslationService(p) for p in llm_ids_by_priority())


# The answer when retrieval finds nothing to answer from. No model is asked:
# given no context, it would answer from its own knowledge, as if the document
# had said it (#31).
NOTHING_FOUND_ANSWER = "I couldn't find anything in this document about that."


def _display_page(metadata: Dict[str, Any]) -> Optional[int]:
    """Page number as a human (and `PdfViewer.scrollToPage`) counts them.

    Chunk metadata stores `page` 0-indexed — it comes straight from
    `ingest.PdfIngestor`, which numbers pages as PyMuPDF does, and is used that
    way internally (`retrieval.PageEvidence.page`). Everything that *leaves* this module is read by a person or
    scrolled to by the viewer, so it converts here, at the single boundary.

    `None` when the chunk carries no usable page — missing, or unparseable.
    Every writer of this metadata stores an int
    (`vector_store.add_chunks`), so that's defensive — but defaulting
    to page 1 would cite the first page with exactly the confidence of a real
    hit, and the viewer would scroll there. An absent page says so instead: the
    chat panel labels it "Page ?" and won't jump.
    """
    try:
        return int(metadata.get('page')) + 1
    except (TypeError, ValueError):
        return None


def _page_label(metadata: Dict[str, Any]) -> str:
    """`_display_page` for prose — the LLM's context block and answer text."""
    page = _display_page(metadata)
    return 'N/A' if page is None else str(page)


def answer_language_instruction(answer_lang: str) -> str:
    """The sentence that sets an answer's language (#31).

    Goes in the answer prompt and its system prompt, and nowhere else. The HyDE
    prompt gets none: what it writes is a search query, and a query in the
    answer's language would miss the words of a document written in another.
    """
    if answer_lang == "auto":
        return "Answer in the same language as the question, whatever language the document is in."
    name = LANGUAGE_DISPLAY_NAMES.get(answer_lang, answer_lang)
    return f"Answer in {name}, whatever language the question or the document is in."


def describe_answer_error(service: TranslationService, error: BaseException) -> str:
    """The sentence the chat panel shows when the answer model fails."""
    label = SERVICE_LABELS.get(service, service.value)
    if is_fatal_translation_error(error):
        return f"{label} rejected the API key, so chat can't answer. Check the key in Settings."
    return f"{label} couldn't write an answer: {error}"


@dataclass(frozen=True)
class AnswerModel:
    """The LLM that writes one question's answer."""

    service: TranslationService
    translator: BaseTranslator
    # The model it runs. Optional only so a test can stand in a translator.
    model: Optional[str] = None


# Pages retrieval may show the answer model as images. Each costs about a
# page of text in tokens (`page_images.MAX_SIDE`), and past the best-ranked
# few, a page is unlikely to be where the answer is.
DEFAULT_MAX_IMAGES = 3
# Chunks each search contributes to the fusion: deep enough that a page found
# by only one of the two searches still gets in.
CANDIDATES = 20
# Chunks shown either side of a hit, on its page.
NEIGHBOURS = 1
# Fused chunks a reranker re-scores. Its cost is linear in this; past ~30 the
# chunks it would promote were never going to reach the evidence anyway.
RERANK_DEPTH = 30
# Thinking counts toward this on a model that thinks by default.
ANSWER_MAX_TOKENS = 4000

# A status that, with "image" in the message, is a model saying it takes no
# images — a text-only model behind OpenRouter or Ollama, say — rather than a
# broken request: 400 (OpenAI, Anthropic, Gemini), 415 and 422 (some proxies).
_IMAGE_REFUSAL_STATUSES = frozenset({400, 415, 422})


def is_image_refusal(error: BaseException) -> bool:
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    return status in _IMAGE_REFUSAL_STATUSES and "image" in str(error).lower()


class _ImagesRefused(Exception):
    pass


class EnhancedRAGChain:
    """Answers a question from one document's index; see the module doc."""

    def __init__(self, vector_store: ChromaDBManager, *, hyde: bool = False,
                 max_images: int = DEFAULT_MAX_IMAGES, candidates: int = CANDIDATES,
                 neighbours: int = NEIGHBOURS, reranker: Optional[Any] = None,
                 rerank_depth: int = RERANK_DEPTH):
        self.vector_store = vector_store
        self.hyde = hyde
        self.max_images = max_images
        self.candidates = candidates
        self.neighbours = neighbours
        # Anything with `scores(question, texts) -> List[float]`; `None`, the
        # app's choice, keeps the first stage's order. A local cross-encoder
        # gained one question in 39 for +0.5 s a question and a 113 MB
        # download (`benchmarks/rag/cross_encoder.py`, `benchmarks/rag/README.md`).
        self.reranker = reranker
        self.rerank_depth = rerank_depth
        self._model: Optional[AnswerModel] = None
        self._model_key: Optional[Tuple[Any, ...]] = None
        self._model_lock = threading.Lock()
        # (provider, model) pairs that refused an image: sent text only from
        # then on, rather than failing and retrying on every question.
        self._text_only: Set[Tuple[str, Optional[str]]] = set()
        logger.info("RAG chain initialized")

    def _answer_model(self) -> Optional[AnswerModel]:
        """The LLM that writes an answer, from the settings as they are now. Blocking.

        Looked up for every question. The chain is built once per process and
        used to pick its model then, so a key saved later was ignored until the
        sidecar restarted: `PUT /config` replaces the settings object rather
        than updating the one the chain held (#31). Building the chain again
        instead is not an option; see `api/routes/rag.py:_recover`.

        Candidates, in order: `rag.answer_model`, the translation model, then
        every LLM by `ProviderSpec.priority` with the model it runs
        (`AppSettings.model_for`). Only an LLM counts, one candidate per
        provider, and the first whose provider has a key answers; `None` with
        no key at all, which is the template-answer path. The translator is
        built again only when the chosen service, its key, its model or its
        endpoint changed — so a new `answer_model` applies from the next
        question, like a new key.
        """
        settings = get_settings()
        refs = [settings.rag.answer_model, settings.translation.model]
        refs += [ModelRef(provider=s, model=settings.model_for(s)) for s in _fallback_services()]
        candidates: List[ModelRef] = []
        for ref in refs:
            if ref is not None and provider(ref.provider.value).is_llm and all(
                ref.provider != c.provider for c in candidates
            ):
                candidates.append(ref)

        with self._model_lock:
            for ref in candidates:
                service = ref.provider
                if not settings.has_api_key(service):
                    continue
                provider_settings = settings.providers[service.value]
                key = (
                    service,
                    provider_settings.api_key,
                    ref.model,
                    provider_settings.base_url,
                )
                if self._model is not None and key == self._model_key:
                    return self._model
                try:
                    # `generate()` never reads the language pair: the answer's
                    # language is set in its prompt.
                    translator = TranslatorFactory.create_translator(
                        service=service,
                        lang_in="auto",
                        lang_out="vi",
                        model=ref.model,
                    )
                except Exception as e:
                    logger.error(f"Failed to initialize {service.value} for RAG: {e}")
                    continue
                self._model, self._model_key = AnswerModel(service, translator, ref.model), key
                logger.info(f"RAG answer LLM initialized: {service.value} {ref.model}")
                return self._model
            self._model = self._model_key = None
            return None

    async def answer_question(self, question: str, index_id: str, document_id: str,
                              document_path: str,
                              answer_lang: str = "auto",
                              max_pdf_sources: int = 5,
                              progress_callback: Optional[Callable[[str, int], None]] = None) -> Dict[str, Any]:
        """Answer a question about one document, from that document's index alone.

        `index_id` names the only ChromaDB collection searched; no other
        document's chunks are within reach (#59). `answer_lang` is a language
        code, or `auto` for the question's own language. `max_pdf_sources` is
        how many pages of evidence the answer is written from. `document_path`
        is where pages shown as images are rendered from; a file gone since it
        was indexed costs the images, not the answer.

        Raises when the question can't be answered: `IndexUnavailableError` when
        the index's chunks can't be read, `AnswerGenerationError` when the model
        fails, anything else as it came. `_run_ask` ends the job with an `error`
        event for each. Returning the failure as the answer, as this used to,
        put it in the chat as if the document had said it (#31).
        """
        logger.info(f"Processing question: {question[:100]}...")
        start_time = datetime.now()

        model = await asyncio.to_thread(self._answer_model)
        evidence = await self.retrieve(question, index_id, max_pdf_sources, model)
        answer, images_sent = await self._generate_answer(
            question, evidence, model, answer_lang, document_path
        )

        citations = link_citations(answer, {page.page + 1 for page in evidence})
        if citations.invalid_pages:
            logger.info("Dropped citations of pages not in the evidence: %s", citations.invalid_pages)
        pdf_references = self._create_pdf_references(
            evidence, citations.cited_pages, document_id, document_path
        )

        processing_time = (datetime.now() - start_time).total_seconds()
        logger.info(f"Question answered successfully in {processing_time:.2f}s")
        # Which model wrote it: the one chosen when the question started, even
        # if the choice changed since. None when no model did — the extractive
        # answer, or nothing found to answer from.
        wrote = model is not None and bool(evidence)
        return {
            'answer': citations.text,
            'provider': model.service.value if wrote else None,
            'model': model.model if wrote else None,
            'pdf_references': pdf_references,
            'quality_metrics': {
                'total_sources': len(evidence),
                'images_sent': images_sent,
                'pages_cited': len(citations.cited_pages),
                'invalid_citations': len(citations.invalid_pages),
            },
            'processing_time': processing_time,
            'sources_used': {
                'pdf_sources': len(evidence),
            },
            'timestamp': datetime.now().isoformat()
        }

    # ------------------------------------------------------------------
    # retrieval
    # ------------------------------------------------------------------

    async def retrieve(self, question: str, index_id: str, max_pages: int,
                       model: Optional[AnswerModel] = None) -> List[PageEvidence]:
        """The `max_pages` pages most likely to answer `question`, best first.

        Raises `IndexUnavailableError` when the index has no chunks to read.
        """
        # The index's chunks, read once. The keyword search and the page
        # grouping both work from this list; reading them again per search
        # used to cost up to 12 reads a question (#59).
        chunks = await self.vector_store.get_chunks(index_id)
        if not chunks:
            # `_run_index` never marks an index with no chunks ready, so this
            # one is damaged.
            raise IndexUnavailableError(index_id)

        # BM25 over what is embedded: each chunk under its section heading.
        # Tokenizing a whole document is CPU work: off the event loop.
        keywords = await asyncio.to_thread(Bm25Index, [contextual_text(c) for c in chunks])

        queries = [question]
        if self.hyde:
            hypothetical = await self._generate_hypothetical_answer(question, model)
            if hypothetical != question:
                queries.append(hypothetical)

        rankings: List[List[str]] = []
        for query in queries:
            semantic = await self.vector_store.search_similar(index_id, query, self.candidates)
            rankings.append([hit['chunk_id'] for hit in semantic])
            scores = await asyncio.to_thread(keywords.scores, query)
            best = sorted(
                (i for i, score in enumerate(scores) if score > 0),
                key=lambda i: scores[i],
                reverse=True,
            )[:self.candidates]
            rankings.append([chunks[i]['chunk_id'] for i in best])

        fused = reciprocal_rank_fusion(rankings)
        if self.reranker is not None and fused:
            by_id = chunk_map(chunks)
            shortlist = [chunk_id for chunk_id, _ in fused[:self.rerank_depth]]
            scores = await asyncio.to_thread(
                self.reranker.scores, question, [contextual_text(by_id[i]) for i in shortlist]
            )
            reranked = sorted(zip(shortlist, scores), key=lambda pair: pair[1], reverse=True)
            # One more vote, fused by rank, not the verdict: the multilingual
            # cross-encoder benchmarked alone ranked Markdown tables below the
            # prose around them, and fused it did better than either alone.
            fused = reciprocal_rank_fusion([shortlist, [chunk_id for chunk_id, _ in reranked]])
        evidence = await asyncio.to_thread(
            group_by_page, fused, chunks, max_pages, self.neighbours
        )
        logger.info("Retrieved pages %s", [page.page + 1 for page in evidence])
        return evidence

    async def _generate_hypothetical_answer(self, question: str,
                                            model: Optional[AnswerModel]) -> str:
        """HyDE, with `hyde=True`: a hypothetical answer to search with.

        The question itself when there is no model, or when the model fails:
        HyDE only improves the search, and a failing model is reported by the
        answer step.
        """
        if model is None:
            return question

        # No language instruction; see `answer_language_instruction`.
        hyde_prompt = f"""Write a brief hypothetical answer to this question, worded as a technical document or research paper would word it. Keep it under 100 words: it is embedded as a search query.

Question: {question}"""

        try:
            answer = await asyncio.to_thread(
                model.translator.generate,
                hyde_prompt,
                "You generate hypothetical answers for document retrieval.",
                # A ceiling, not a length: the prompt's word limit sets that.
                # A model that thinks by default counts thinking toward it, and
                # at 150 could spend it all and silently skip HyDE.
                1024,
            )
        except Exception as e:
            logger.warning(f"HyDE generation failed: {e}")
            return question
        return answer or question

    # ------------------------------------------------------------------
    # the answer
    # ------------------------------------------------------------------

    def _sends_images(self, model: AnswerModel) -> bool:
        return (
            self.max_images > 0
            and provider(model.service.value).accepts_images
            and (model.service.value, model.model) not in self._text_only
        )

    async def _generate_answer(self, question: str, evidence: Sequence[PageEvidence],
                               model: Optional[AnswerModel], answer_lang: str,
                               document_path: Optional[str] = None) -> Tuple[str, int]:
        """The answer, before its citations are checked, and how many page
        images the model was shown to write it.

        A model that fails raises `AnswerGenerationError`. It used to fall back
        to the template answer, which hid a rejected key behind excerpts that
        looked like an answer.
        """
        if not evidence:
            return NOTHING_FOUND_ANSWER, 0
        if model is None:
            return self._extractive_answer(evidence), 0

        images: List[PageImage] = []
        if self._sends_images(model) and document_path:
            picked = select_image_pages(evidence, self.max_images)
            if picked:
                images = await asyncio.to_thread(
                    render_pages, Path(document_path), [page.page for page in picked]
                )

        prompt = self._create_answer_prompt(question, evidence, answer_lang, images)
        try:
            answer = await self._generate_with_llm(model, prompt, answer_lang, images)
        except _ImagesRefused:
            logger.warning(
                "%s %s refused page images; it is sent text only from now on",
                model.service.value, model.model,
            )
            self._text_only.add((model.service.value, model.model))
            images = []
            prompt = self._create_answer_prompt(question, evidence, answer_lang, images)
            answer = await self._generate_with_llm(model, prompt, answer_lang, images)
        return answer, len(images)

    def _create_answer_prompt(self, question: str, evidence: Sequence[PageEvidence],
                              answer_lang: str = "auto",
                              images: Sequence[PageImage] = ()) -> str:
        """The prompt for one answer.

        Every evidence page, whole, under its page number: the chat lists each
        as a reference, so the answer must have read them all, and the number
        is what it cites. Pages first and the question after them, so the
        instruction the model reads last is about this question. The math
        delimiters are named because the chat's remark-math reads only `$…$`
        and `$$…$$`; a model's `\\(…\\)` would show as raw text.
        """
        blocks = []
        for page in evidence:
            heading = next(
                (c['metadata'].get('heading') for c in page.chunks if c['metadata'].get('heading')),
                "",
            )
            title = f"--- page {page.page + 1}" + (f" (section: {heading})" if heading else "") + " ---"
            body = "\n\n".join(chunk['text'] for chunk in page.chunks)
            blocks.append(f"{title}\n{body}")
        attached = ""
        if images:
            labels = ", ".join(image.label or "" for image in images)
            attached = (
                f"\n\nImages of some of these pages are attached: {labels}. Read charts, "
                "figures, tables and scanned text from the images; for those pages the "
                "text above may be incomplete or garbled."
            )
        pages = "\n\n".join(blocks)

        return f"""Pages of the document, with the text found on each:

{pages}{attached}

Question: {question}

Answer from these pages only, directly and concisely, without headed sections. After each statement, cite the page it comes from as [p. N], or [p. N, M] for several; cite only pages listed above. If these pages don't contain the answer, say so plainly rather than answering from general knowledge. The chat renders Markdown, and math written as $...$ or $$...$$, so use a list, table or formula where it makes the answer clearer. {answer_language_instruction(answer_lang)}"""

    async def _generate_with_llm(self, model: AnswerModel, prompt: str, answer_lang: str,
                                 images: Sequence[PageImage] = ()) -> str:
        """Generate an answer with the configured LLM.

        Raises `AnswerGenerationError`, carrying the sentence to show, when the
        model fails or returns nothing; `_ImagesRefused` when it failed because
        it takes no images. `images` is passed only when there are some, so a
        backend written before images existed is called as it always was.
        """
        system = (
            "You answer questions about one PDF document from pages of it, and cite "
            "the page each statement comes from as [p. N]. "
            + answer_language_instruction(answer_lang)
        )
        kwargs = {"images": list(images)} if images else {}
        try:
            answer = await asyncio.to_thread(
                model.translator.generate, prompt, system, ANSWER_MAX_TOKENS, **kwargs
            )
        except Exception as e:
            if images and is_image_refusal(e):
                raise _ImagesRefused() from e
            logger.error(f"Answer generation failed: {e}")
            raise AnswerGenerationError(describe_answer_error(model.service, e)) from e
        if not answer:
            label = SERVICE_LABELS.get(model.service, model.service.value)
            raise AnswerGenerationError(f"{label} returned an empty answer. Try asking again.")
        return answer

    def _extractive_answer(self, evidence: Sequence[PageEvidence]) -> str:
        """The answer with no LLM key: the passages that matched best, each
        with its page. No model, so no synthesis, and nothing worded as one."""
        parts = ["No answer model is set up, so here are the passages that match best:"]
        for page in evidence[:3]:
            excerpt = _best_chunk(page)['text']
            if len(excerpt) > 300:
                excerpt = excerpt[:300].rsplit(" ", 1)[0] + "…"
            parts.append(f"- {excerpt} {page_link(page.page + 1)}")
        parts.append("\nAdd an API key in Settings → Models for written answers.")
        return "\n".join(parts)

    def _create_pdf_references(self, evidence: Sequence[PageEvidence], cited_pages: Sequence[int],
                               document_id: str, document_path: str) -> List[Dict[str, Any]]:
        """One reference per evidence page: the cited ones first, in the order
        the answer cites them, then the rest in retrieval order.

        This list is the `pdf_references` field of the `answer`/`done` SSE
        payload, and the chat panel feeds `page` straight to
        `PdfViewer.scrollToPage` — so it is 1-indexed, or `None`. See
        `_display_page`. Every source comes from one document's index, so its
        id and path come from the caller; chunks don't carry them.
        """
        order = {page: i for i, page in enumerate(cited_pages)}
        ranked = sorted(
            enumerate(evidence),
            key=lambda item: (
                0 if item[1].page + 1 in order else 1,
                order.get(item[1].page + 1, item[0]),
            ),
        )
        references = []
        for _, page in ranked:
            best = _best_chunk(page)
            metadata = best.get('metadata', {})
            text = best.get('text', '')
            reference = {
                'type': 'pdf',
                'page': _display_page(metadata),
                'text': text[:150] + ("..." if len(text) > 150 else ""),
                'confidence': page.score,
                'document_id': document_id,
                'document_path': document_path,
                'chunk_id': best.get('chunk_id', ''),
                'cited': page.page + 1 in order,
                'page_kind': page.page_kind,
                'shown_as_image': page.wants_image,
                'has_equations': any(c['metadata'].get('has_equations') for c in page.chunks),
                'has_tables': any(c['metadata'].get('has_tables') for c in page.chunks),
                'has_figures': any(c['metadata'].get('has_figures') for c in page.chunks),
            }
            if 'bbox' in metadata:
                try:
                    reference['bbox'] = json.loads(metadata['bbox'])
                except (TypeError, ValueError):
                    pass
            references.append(reference)
        return references


def _best_chunk(page: PageEvidence) -> Dict[str, Any]:
    """The page's best-matching chunk."""
    for chunk in page.chunks:
        if chunk['chunk_id'] == page.best_chunk_id:
            return chunk
    return page.chunks[0]
