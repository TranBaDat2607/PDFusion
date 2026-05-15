"""
RAG chain that answers questions over indexed PDF documents.
"""

import logging
from typing import List, Dict, Any, Optional
import json
from datetime import datetime

from ..config import get_settings
from ..translators import TranslatorFactory
from .vector_store import ChromaDBManager
from .reference_manager import ReferenceManager

logger = logging.getLogger(__name__)


class EnhancedRAGChain:
    """RAG chain that retrieves PDF context from ChromaDB and synthesizes an answer via an LLM translator."""

    def __init__(self, vector_store: ChromaDBManager):
        self.vector_store = vector_store
        self.reference_manager = ReferenceManager()
        self.settings = get_settings()

        self.translator = None
        self._initialize_translator()

        logger.info("RAG chain initialized")

    def _initialize_translator(self):
        """Initialize the translator for answer generation."""
        try:
            preferred_service = self.settings.translation.preferred_service
            self.translator = TranslatorFactory.create_translator(
                service=preferred_service,
                lang_in="auto",
                lang_out="vi"
            )
            logger.info(f"Translator initialized: {preferred_service}")
        except Exception as e:
            logger.error(f"Failed to initialize translator: {e}")

    async def answer_question(self, question: str, document_id: Optional[str] = None,
                            max_pdf_sources: int = 5,
                            progress_callback: Optional[callable] = None) -> Dict[str, Any]:
        """Answer a question using indexed PDF knowledge."""
        logger.info(f"Processing question: {question[:100]}...")

        start_time = datetime.now()

        try:
            pdf_sources = await self._retrieve_pdf_knowledge(
                question, document_id, max_pdf_sources
            )

            answer = await self._generate_answer(question, pdf_sources)

            pdf_references = self._create_pdf_references(pdf_sources)

            quality_metrics = self._calculate_quality_metrics(pdf_sources)

            processing_time = (datetime.now() - start_time).total_seconds()

            result = {
                'answer': answer,
                'pdf_references': pdf_references,
                'quality_metrics': quality_metrics,
                'processing_time': processing_time,
                'sources_used': {
                    'pdf_sources': len(pdf_sources),
                },
                'timestamp': datetime.now().isoformat()
            }

            logger.info(f"Question answered successfully in {processing_time:.2f}s")
            return result

        except Exception as e:
            logger.error(f"Failed to answer question: {e}")
            return {
                'answer': f"Sorry, I cannot answer this question due to an error: {str(e)}",
                'pdf_references': [],
                'quality_metrics': {'confidence': 0.0, 'completeness': 0.0},
                'error': str(e)
            }

    async def _retrieve_pdf_knowledge(self, question: str, document_id: Optional[str],
                                    max_sources: int) -> List[Dict[str, Any]]:
        """Retrieve relevant knowledge from PDF documents."""

        try:
            if document_id:
                filter_metadata = {"document_id": document_id}
            else:
                filter_metadata = None

            # Stage 0: HyDE - Generate hypothetical answer
            hypothetical_answer = await self._generate_hypothetical_answer(question)

            # Stage 1: Dual retrieval — original question + HyDE answer
            results_original = await self.vector_store.hybrid_search(
                query=question,
                n_results=max_sources * 2,
                alpha=0.5,
                filter_metadata=filter_metadata
            )

            results_hyde = await self.vector_store.hybrid_search(
                query=hypothetical_answer,
                n_results=max_sources * 2,
                alpha=0.7,  # higher semantic weight for HyDE
                filter_metadata=filter_metadata
            )

            candidate_results = self._merge_search_results(results_original, results_hyde, max_sources * 3)

            # Stage 2: Add surrounding context to top candidates
            enriched_results = await self._add_surrounding_context(
                candidate_results[:max_sources * 2],
                document_id,
                context_window=1
            )

            # Stage 3: Re-rank
            results = await self._rerank_results(
                question,
                enriched_results,
                top_k=max_sources
            )

            logger.info(f"Retrieved {len(results)} PDF sources")
            return results

        except Exception as e:
            logger.error(f"PDF knowledge retrieval failed: {e}")
            return []

    async def _generate_hypothetical_answer(self, question: str) -> str:
        """HyDE: generate a hypothetical answer to use as the retrieval query."""
        try:
            if not self.translator:
                return question

            hyde_prompt = f"""Generate a brief hypothetical answer to this question. The answer should be written as if it came from a technical document or research paper. Keep it under 100 words.

Question: {question}

Hypothetical answer:"""

            if hasattr(self.translator, 'client') and hasattr(self.translator.client, 'chat'):
                response = self.translator.client.chat.completions.create(
                    model=self.translator.model,
                    messages=[
                        {"role": "system", "content": "You generate hypothetical answers for document retrieval."},
                        {"role": "user", "content": hyde_prompt}
                    ],
                    max_tokens=150,
                    temperature=0.3
                )
                return response.choices[0].message.content.strip()
            elif hasattr(self.translator, 'model'):
                response = self.translator.model.generate_content(hyde_prompt)
                return response.text.strip()
            else:
                return question

        except Exception as e:
            logger.error(f"HyDE generation failed: {e}")
            return question

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

    async def _add_surrounding_context(self, chunks: List[Dict[str, Any]],
                                      document_id: Optional[str],
                                      context_window: int = 1) -> List[Dict[str, Any]]:
        """Add surrounding chunks to provide better context."""
        if not document_id or not chunks:
            return chunks

        enriched_chunks = []

        for chunk in chunks:
            metadata = chunk.get('metadata', {})
            page = metadata.get('page', 0)
            chunk_index = metadata.get('chunk_index', 0)

            surrounding = await self.vector_store.get_document_chunks(
                document_id=document_id,
                page_range=(max(0, page - 1), page + 1),
                limit=None
            )

            context_before = []
            context_after = []

            for surr_chunk in surrounding:
                surr_meta = surr_chunk.get('metadata', {})
                surr_page = surr_meta.get('page', 0)
                surr_index = surr_meta.get('chunk_index', 0)

                if surr_page == page and surr_index < chunk_index and chunk_index - surr_index <= context_window:
                    context_before.append(surr_chunk['text'])

                if surr_page == page and surr_index > chunk_index and surr_index - chunk_index <= context_window:
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

    async def _generate_answer(self, question: str, pdf_sources: List[Dict[str, Any]]) -> str:
        """Generate an answer using PDF sources."""

        context_parts = []

        if pdf_sources:
            context_parts.append("=== INFORMATION FROM PDF DOCUMENTS ===")
            for i, source in enumerate(pdf_sources[:3]):
                text = source.get('text', '')
                page = source.get('metadata', {}).get('page', 'N/A')
                context_parts.append(f"[PDF Source {i+1}, Page {page}]: {text[:300]}...")

        full_context = '\n'.join(context_parts)

        prompt = self._create_answer_prompt(question, full_context)

        try:
            if self.translator:
                answer = await self._generate_with_llm(prompt)
            else:
                answer = self._generate_template_answer(question, pdf_sources)
            return answer
        except Exception as e:
            logger.error(f"Answer generation failed: {e}")
            return self._generate_template_answer(question, pdf_sources)

    def _create_answer_prompt(self, question: str, context: str) -> str:
        """Create a comprehensive prompt for answer generation."""

        prompt = f"""
You are an intelligent AI assistant specialized in answering questions based on translated PDF documents.

TASK:
- Answer questions comprehensively and accurately
- Base your answer on the PDF context provided
- Answer in Vietnamese

QUESTION: {question}

AVAILABLE INFORMATION:
{context}

ANSWER REQUIREMENTS:
Answer the question concisely, accurately, and completely. Provide only the final answer without dividing into multiple sections or detailed explanations.

ANSWER:
"""

        return prompt

    async def _generate_with_llm(self, prompt: str) -> str:
        """Generate answer using LLM (OpenAI/Gemini)."""

        try:
            if hasattr(self.translator, 'client'):
                # OpenAI-compatible
                if hasattr(self.translator.client, 'chat'):
                    response = self.translator.client.chat.completions.create(
                        model=self.translator.model,
                        messages=[
                            {"role": "system", "content": "You are an intelligent AI assistant that answers questions based on documents. Always respond in Vietnamese regardless of the language of the question or source documents."},
                            {"role": "user", "content": prompt}
                        ],
                        max_tokens=1000,
                        temperature=0.3
                    )
                    return response.choices[0].message.content

                # Gemini
                elif hasattr(self.translator, 'model'):
                    response = self.translator.model.generate_content(prompt)
                    return response.text

            return "Unable to generate answer with LLM."

        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            raise

    def _generate_template_answer(self, question: str, pdf_sources: List[Dict[str, Any]]) -> str:
        """Generate template-based answer as fallback."""

        answer_parts = []
        answer_parts.append(f"Based on available information, I will answer the question: '{question}'")

        if pdf_sources:
            answer_parts.append("\n**Information from PDF documents:**")
            for i, source in enumerate(pdf_sources[:2]):
                text = source.get('text', '')
                page = source.get('metadata', {}).get('page', 'N/A')
                answer_parts.append(f"- Page {page}: {text[:200]}...")
            answer_parts.append("\n**Conclusion:** The above information provides an overview of your question. For more details, please refer to the cited sources.")
        else:
            answer_parts.append("\nSorry, I could not find relevant information to answer this question.")

        return '\n'.join(answer_parts)

    def _create_pdf_references(self, pdf_sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Create PDF references with navigation information."""

        references = []

        for source in pdf_sources:
            metadata = source.get('metadata', {})

            reference = {
                'type': 'pdf',
                'page': metadata.get('page', 0),
                'text': source.get('text', '')[:150] + "...",
                'confidence': source.get('similarity_score', 0.0),
                'document_id': metadata.get('document_id', ''),
                'document_path': metadata.get('document_path', ''),
                'chunk_id': source.get('chunk_id', ''),
                'has_equations': metadata.get('has_equations', False),
                'has_tables': metadata.get('has_tables', False),
                'has_figures': metadata.get('has_figures', False)
            }

            if 'elements' in metadata:
                try:
                    elements = json.loads(metadata['elements'])
                    if elements and len(elements) > 0:
                        first_element = elements[0]
                        if 'bbox' in first_element:
                            reference['bbox'] = first_element['bbox']
                except Exception:
                    pass

            references.append(reference)

        return references

    def _calculate_quality_metrics(self, pdf_sources: List[Dict[str, Any]]) -> Dict[str, float]:
        """Calculate quality metrics for the answer."""

        return {
            'total_sources': len(pdf_sources)
        }
