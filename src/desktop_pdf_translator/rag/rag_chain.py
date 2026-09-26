"""
RAG chain that answers a question from one indexed PDF document.
"""

import asyncio
import json
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import ModelRef, TranslationService, get_settings
from ..providers.registry import llm_ids_by_priority, provider
from ..translators.base import (
    LANGUAGE_DISPLAY_NAMES,
    BaseTranslator,
    is_fatal_translation_error,
)
from ..translators.capabilities import SERVICE_LABELS
from ..translators.factory import TranslatorFactory
from .errors import AnswerGenerationError, IndexUnavailableError
from .keyword_search import Bm25Index
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
    `document_processor.process_pdf`'s `range(len(doc))` and is used that way
    internally (`_add_surrounding_context`'s page window, the re-rank
    page score). Everything that *leaves* this module is read by a person or
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


class EnhancedRAGChain:
    """RAG chain that retrieves PDF context from ChromaDB and synthesizes an answer via an LLM translator."""

    def __init__(self, vector_store: ChromaDBManager):
        self.vector_store = vector_store
        self._model: Optional[AnswerModel] = None
        self._model_key: Optional[Tuple[Any, ...]] = None
        self._model_lock = threading.Lock()
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
        code, or `auto` for the question's own language.

        Raises when the question can't be answered: `IndexUnavailableError` when
        the index's chunks can't be read, `AnswerGenerationError` when the model
        fails, anything else as it came. `_run_ask` ends the job with an `error`
        event for each. Returning the failure as the answer, as this used to,
        put it in the chat as if the document had said it (#31).
        """
        logger.info(f"Processing question: {question[:100]}...")

        start_time = datetime.now()

        model = await asyncio.to_thread(self._answer_model)

        pdf_sources = await self._retrieve_pdf_knowledge(
            question, index_id, max_pdf_sources, model
        )

        answer = await self._generate_answer(question, pdf_sources, model, answer_lang)

        pdf_references = self._create_pdf_references(pdf_sources, document_id, document_path)

        quality_metrics = self._calculate_quality_metrics(pdf_sources)

        processing_time = (datetime.now() - start_time).total_seconds()

        logger.info(f"Question answered successfully in {processing_time:.2f}s")
        # Which model wrote it: the one chosen when the question started, even
        # if the choice changed since. None when no model did — the template
        # answer, or nothing found to answer from.
        wrote = model is not None and bool(pdf_sources)
        return {
            'answer': answer,
            'provider': model.service.value if wrote else None,
            'model': model.model if wrote else None,
            'pdf_references': pdf_references,
            'quality_metrics': quality_metrics,
            'processing_time': processing_time,
            'sources_used': {
                'pdf_sources': len(pdf_sources),
            },
            'timestamp': datetime.now().isoformat()
        }

    async def _retrieve_pdf_knowledge(self, question: str, index_id: str,
                                      max_sources: int,
                                      model: Optional[AnswerModel] = None) -> List[Dict[str, Any]]:
        """Retrieve relevant knowledge from one document's index. Raises on failure."""

        # The index's chunks, read once. Both keyword passes and the
        # surrounding-context step work from this list; they used to re-read
        # all of the document's chunks, up to 12 times a question (#59).
        chunks = await self.vector_store.get_chunks(index_id)
        if not chunks:
            # `_run_index` never marks an index with no chunks ready, so this
            # one is damaged.
            raise IndexUnavailableError(index_id)

        # BM25 statistics for those chunks, built once for both passes.
        # Tokenizing a whole document is CPU work: off the event loop.
        keywords = await asyncio.to_thread(Bm25Index, [chunk['text'] for chunk in chunks])

        # Stage 0: HyDE - Generate hypothetical answer
        hypothetical_answer = await self._generate_hypothetical_answer(question, model)

        # Stage 1: Dual retrieval — original question + HyDE answer
        results_original = await self.vector_store.hybrid_search(
            index_id,
            query=question,
            chunks=chunks,
            keywords=keywords,
            n_results=max_sources * 2,
            alpha=0.5,
        )

        # With no hypothetical answer, the second pass would search for the
        # question again.
        results_hyde: List[Dict[str, Any]] = []
        if hypothetical_answer != question:
            results_hyde = await self.vector_store.hybrid_search(
                index_id,
                query=hypothetical_answer,
                chunks=chunks,
                keywords=keywords,
                n_results=max_sources * 2,
                alpha=0.7,  # higher semantic weight for HyDE
            )

        candidate_results = self._merge_search_results(results_original, results_hyde, max_sources * 3)

        # Stage 2: Add surrounding context to top candidates. It groups every
        # chunk by page, so it runs off the event loop too.
        enriched_results = await asyncio.to_thread(
            self._add_surrounding_context,
            candidate_results[:max_sources * 2],
            chunks,
            1,
        )

        # Stage 3: Re-rank
        results = await self._rerank_results(
            question,
            enriched_results,
            top_k=max_sources
        )

        logger.info(f"Retrieved {len(results)} PDF sources")
        return results

    async def _generate_hypothetical_answer(self, question: str,
                                            model: Optional[AnswerModel]) -> str:
        """HyDE: a hypothetical answer to search with.

        The question itself when there is no model, or when the model fails:
        HyDE only improves the search, and a failing model is reported by the
        answer step.
        """
        if model is None:
            return question

        # No language instruction; see `answer_language_instruction`.
        hyde_prompt = f"""Generate a brief hypothetical answer to this question. The answer should be written as if it came from a technical document or research paper. Keep it under 100 words.

Question: {question}

Hypothetical answer:"""

        try:
            answer = await asyncio.to_thread(
                model.translator.generate,
                hyde_prompt,
                "You generate hypothetical answers for document retrieval.",
                150,
            )
        except Exception as e:
            logger.warning(f"HyDE generation failed: {e}")
            return question
        return answer or question

    def _merge_search_results(self, results1: List[Dict[str, Any]],
                              results2: List[Dict[str, Any]],
                              max_results: int) -> List[Dict[str, Any]]:
        """Merge and deduplicate results from multiple searches."""
        seen_ids = set()
        merged = []

        for result in results1 + results2:
            chunk_id = result.get('chunk_id')
            if chunk_id and chunk_id not in seen_ids:
                seen_ids.add(chunk_id)
                merged.append(result)

        merged.sort(key=lambda x: x.get('final_score', x.get('similarity_score', 0)), reverse=True)

        return merged[:max_results]

    def _add_surrounding_context(self, candidates: List[Dict[str, Any]],
                                 chunks: List[Dict[str, Any]],
                                 context_window: int = 1) -> List[Dict[str, Any]]:
        """Add each candidate's neighbouring chunks on the same page.

        Works from the index's chunks, already read, instead of reading them
        again for every candidate.
        """
        if not candidates:
            return candidates

        chunks_by_page = defaultdict(list)
        for surr_chunk in chunks:
            chunks_by_page[surr_chunk.get('metadata', {}).get('page', 0)].append(surr_chunk)

        enriched_chunks = []

        for chunk in candidates:
            metadata = chunk.get('metadata', {})
            page = metadata.get('page', 0)
            chunk_index = metadata.get('chunk_index', 0)

            context_before = []
            context_after = []

            for surr_chunk in chunks_by_page.get(page, []):
                surr_index = surr_chunk.get('metadata', {}).get('chunk_index', 0)

                if surr_index < chunk_index and chunk_index - surr_index <= context_window:
                    context_before.append(surr_chunk['text'])

                if surr_index > chunk_index and surr_index - chunk_index <= context_window:
                    context_after.append(surr_chunk['text'])

            enriched_text_parts = []
            if context_before:
                enriched_text_parts.append("...\n" + "\n".join(context_before) + "\n")
            enriched_text_parts.append(chunk['text'])
            if context_after:
                enriched_text_parts.append("\n" + "\n".join(context_after) + "\n...")

            enriched_chunk = chunk.copy()
            enriched_chunk['text'] = "".join(enriched_text_parts)
            enriched_chunk['original_text'] = chunk['text']
            enriched_chunks.append(enriched_chunk)

        return enriched_chunks

    async def _rerank_results(self, question: str, chunks: List[Dict[str, Any]],
                             top_k: int = 5) -> List[Dict[str, Any]]:
        """Re-rank results using multiple signals for better relevance."""
        if not chunks:
            return []

        question_lower = question.lower()
        question_words = set(question_lower.split())

        metadata_keywords = {
            'en': ['title', 'author', 'abstract', 'summary', 'introduction', 'conclusion'],
            'vi': ['tiêu đề', 'tác giả', 'tóm tắt', 'giới thiệu', 'kết luận']
        }
        is_metadata_query = any(
            kw in question_lower
            for keywords in metadata_keywords.values()
            for kw in keywords
        )

        for chunk in chunks:
            text_lower = chunk.get('original_text', chunk.get('text', '')).lower()
            metadata = chunk.get('metadata', {})
            page = metadata.get('page', 100)
            section_type = metadata.get('chunk_type', metadata.get('section_type', 'content'))
            chunk_index = metadata.get('chunk_index', 100)

            base_score = chunk.get('final_score', chunk.get('similarity_score', 0))

            words_in_text = set(text_lower.split())
            keyword_matches = len(question_words & words_in_text)
            keyword_density = keyword_matches / max(len(question_words), 1)

            page_score = 1.0 / (1.0 + page * 0.1)
            index_score = 1.0 / (1.0 + chunk_index * 0.05)

            text_length = len(chunk.get('text', ''))
            length_score = min(text_length / 500, 1.0)

            section_score = 0.0
            if is_metadata_query:
                if section_type == 'title':
                    section_score = 1.0
                elif section_type == 'header':
                    section_score = 0.8
                elif section_type == 'abstract':
                    section_score = 0.9
            else:
                if section_type == 'content':
                    section_score = 0.3

            if is_metadata_query:
                rerank_score = (
                    0.15 * base_score +
                    0.05 * keyword_density +
                    0.2 * page_score +
                    0.2 * index_score +
                    0.1 * length_score +
                    0.3 * section_score
                )
            else:
                rerank_score = (
                    0.4 * base_score +
                    0.3 * keyword_density +
                    0.15 * page_score +
                    0.05 * index_score +
                    0.05 * length_score +
                    0.05 * section_score
                )

            chunk['rerank_score'] = rerank_score
            chunk['final_score'] = rerank_score
            chunk['is_metadata_query'] = is_metadata_query

        chunks.sort(key=lambda x: x.get('rerank_score', 0), reverse=True)

        return chunks[:top_k]

    async def _generate_answer(self, question: str, pdf_sources: List[Dict[str, Any]],
                               model: Optional[AnswerModel], answer_lang: str) -> str:
        """Write the answer from the retrieved sources.

        A model that fails raises `AnswerGenerationError`. It used to fall back
        to the template answer, which hid a rejected key behind excerpts that
        looked like an answer.
        """
        if not pdf_sources:
            return NOTHING_FOUND_ANSWER
        if model is None:
            return self._generate_template_answer(question, pdf_sources)

        context_parts = ["=== INFORMATION FROM PDF DOCUMENTS ==="]
        for i, source in enumerate(pdf_sources[:3]):
            text = source.get('text', '')
            page = _page_label(source.get('metadata', {}))
            context_parts.append(f"[PDF Source {i+1}, Page {page}]: {text[:300]}...")

        prompt = self._create_answer_prompt(question, '\n'.join(context_parts), answer_lang)
        return await self._generate_with_llm(model, prompt, answer_lang)

    def _create_answer_prompt(self, question: str, context: str, answer_lang: str = "auto") -> str:
        """Create a comprehensive prompt for answer generation."""

        prompt = f"""
You are an intelligent AI assistant specialized in answering questions based on PDF documents.

TASK:
- Answer questions comprehensively and accurately
- Base your answer on the PDF context provided
- {answer_language_instruction(answer_lang)}

QUESTION: {question}

AVAILABLE INFORMATION:
{context}

ANSWER REQUIREMENTS:
Answer the question concisely, accurately, and completely. Provide only the final answer without dividing into multiple sections or detailed explanations.

ANSWER:
"""

        return prompt

    async def _generate_with_llm(self, model: AnswerModel, prompt: str, answer_lang: str) -> str:
        """Generate an answer with the configured LLM.

        Raises `AnswerGenerationError`, carrying the sentence to show, when the
        model fails or returns nothing.
        """
        system = (
            "You are an intelligent AI assistant that answers questions based on "
            "documents. " + answer_language_instruction(answer_lang)
        )
        try:
            answer = await asyncio.to_thread(model.translator.generate, prompt, system, 1000)
        except Exception as e:
            logger.error(f"Answer generation failed: {e}")
            raise AnswerGenerationError(describe_answer_error(model.service, e)) from e
        if not answer:
            label = SERVICE_LABELS.get(model.service, model.service.value)
            raise AnswerGenerationError(f"{label} returned an empty answer. Try asking again.")
        return answer

    def _generate_template_answer(self, question: str, pdf_sources: List[Dict[str, Any]]) -> str:
        """The answer with no LLM key: the question and excerpts of the best sources."""

        answer_parts = [f"Based on available information, I will answer the question: '{question}'"]
        answer_parts.append("\n**Information from PDF documents:**")
        for source in pdf_sources[:2]:
            text = source.get('text', '')
            page = _page_label(source.get('metadata', {}))
            answer_parts.append(f"- Page {page}: {text[:200]}...")
        answer_parts.append("\n**Conclusion:** The above information provides an overview of your question. For more details, please refer to the cited sources.")

        return '\n'.join(answer_parts)

    def _create_pdf_references(self, pdf_sources: List[Dict[str, Any]],
                               document_id: str, document_path: str) -> List[Dict[str, Any]]:
        """Create PDF references with navigation information.

        This list is the `pdf_references` field of the `answer`/`done` SSE
        payload, and the chat panel feeds `page` straight to
        `PdfViewer.scrollToPage` — so it is 1-indexed, or `None`. See
        `_display_page`.

        Every source comes from one document's index, so its id and path come
        from the caller; chunks no longer carry them.
        """

        references = []

        for source in pdf_sources:
            metadata = source.get('metadata', {})

            reference = {
                'type': 'pdf',
                'page': _display_page(metadata),
                'text': source.get('text', '')[:150] + "...",
                'confidence': source.get('similarity_score', 0.0),
                'document_id': document_id,
                'document_path': document_path,
                'chunk_id': source.get('chunk_id', ''),
                'has_equations': metadata.get('has_equations', False),
                'has_tables': metadata.get('has_tables', False),
                'has_figures': metadata.get('has_figures', False)
            }

            if 'bbox' in metadata:
                try:
                    reference['bbox'] = json.loads(metadata['bbox'])
                except (TypeError, ValueError):
                    pass

            references.append(reference)

        return references

    def _calculate_quality_metrics(self, pdf_sources: List[Dict[str, Any]]) -> Dict[str, float]:
        """Calculate quality metrics for the answer."""

        return {
            'total_sources': len(pdf_sources)
        }
