"""
Enhanced RAG chain that combines PDF knowledge with web research.
Provides comprehensive answers with proper citations and references.
"""

import logging
import asyncio
from typing import List, Dict, Any, Optional, Tuple
import json
from datetime import datetime

from ..config import get_settings
from ..translators import TranslatorFactory
from .vector_store import ChromaDBManager
from .web_research import WebResearchEngine, WebSource
from .reference_manager import ReferenceManager
from .deep_search import DeepSearchEngine, DeepSearchResult
from .academic_apis import AcademicAPIManager
from .paper_cache import PaperCache

logger = logging.getLogger(__name__)


class EnhancedRAGChain:
    """
    Enhanced RAG chain that combines document knowledge with web research
    to provide comprehensive, well-cited answers.
    """
    
    def __init__(self, vector_store: ChromaDBManager, web_research: WebResearchEngine):
        """
        Initialize the RAG chain.

        Args:
            vector_store: ChromaDB manager for document retrieval
            web_research: Web research engine for external knowledge
        """
        self.vector_store = vector_store
        self.web_research = web_research
        self.reference_manager = ReferenceManager()
        self.settings = get_settings()

        # Initialize translator for answer generation
        self.translator = None
        self._initialize_translator()

        # Initialize Deep Search components
        self.deep_search_engine = None
        if self.settings.deep_search.enabled:
            self._initialize_deep_search()

        logger.info("Enhanced RAG chain initialized")
    
    def _initialize_translator(self):
        """Initialize the translator for answer generation."""
        try:
            preferred_service = self.settings.translation.preferred_service
            self.translator = TranslatorFactory.create_translator(
                service=preferred_service,
                lang_in="auto",
                lang_out="vi"  # Vietnamese output by default
            )
            logger.info(f"Translator initialized: {preferred_service}")
        except Exception as e:
            logger.error(f"Failed to initialize translator: {e}")

    def _initialize_deep_search(self):
        """Initialize Deep Search components."""
        try:
            # Initialize paper cache
            cache = PaperCache(
                cache_dir=self.settings.deep_search.cache_dir,
                ttl_days=self.settings.deep_search.cache_ttl_days
            )

            # Initialize academic API manager
            api_manager = AcademicAPIManager(
                pubmed_api_key=self.settings.deep_search.pubmed_api_key,
                core_api_key=self.settings.deep_search.core_api_key
            )

            # Initialize Deep Search Engine
            self.deep_search_engine = DeepSearchEngine(
                academic_api_manager=api_manager,
                vector_store_manager=self.vector_store,
                paper_cache=cache,
                llm_client=self.translator,
                settings=self.settings.deep_search
            )

            logger.info("Deep Search engine initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Deep Search: {e}")
            self.deep_search_engine = None
    
    async def answer_question(self, question: str, document_id: Optional[str] = None,
                            include_web_research: bool = True,
                            max_pdf_sources: int = 5,
                            max_web_sources: int = 5,
                            use_deep_search: bool = False,
                            progress_callback: Optional[callable] = None) -> Dict[str, Any]:
        """
        Answer a question using PDF knowledge and web research.

        Args:
            question: User's question
            document_id: Specific document to search (optional)
            include_web_research: Whether to include web research
            max_pdf_sources: Maximum PDF sources to retrieve
            max_web_sources: Maximum web sources to retrieve
            use_deep_search: Whether to use Deep Search (multi-hop academic search)
            progress_callback: Optional callback for progress updates

        Returns:
            Comprehensive answer with references
        """
        logger.info(f"Processing question: {question[:100]}... (Deep Search: {use_deep_search})")

        start_time = datetime.now()

        try:
            # If Deep Search is enabled and available, use it
            if use_deep_search and self.deep_search_engine:
                return await self._answer_with_deep_search(
                    question, document_id, progress_callback
                )

            # Otherwise, use standard RAG
            # Step 1: Retrieve relevant PDF content
            pdf_sources = await self._retrieve_pdf_knowledge(
                question, document_id, max_pdf_sources
            )
            
            # Step 2: Perform web research if enabled
            web_sources = []
            if include_web_research:
                pdf_context = self._extract_pdf_context(pdf_sources)
                web_sources = await self.web_research.research_topic(question, pdf_context)
                web_sources = web_sources[:max_web_sources]
            
            # Step 3: Generate comprehensive answer
            answer = await self._generate_answer(question, pdf_sources, web_sources)

            # Step 4: Create references
            pdf_references = self._create_pdf_references(pdf_sources)
            web_references = self._create_web_references(web_sources)
            
            # Step 5: Calculate confidence and quality metrics
            quality_metrics = self._calculate_quality_metrics(pdf_sources, web_sources)
            
            processing_time = (datetime.now() - start_time).total_seconds()

            result = {
                'answer': answer,
                'pdf_references': pdf_references,
                'web_references': web_references,
                'quality_metrics': quality_metrics,
                'processing_time': processing_time,
                'sources_used': {
                    'pdf_sources': len(pdf_sources),
                    'web_sources': len(web_sources)
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
                'web_references': [],
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

            # Stage 1: Dual retrieval
            # Search with original question
            results_original = await self.vector_store.hybrid_search(
                query=question,
                n_results=max_sources * 2,
                alpha=0.5,
                filter_metadata=filter_metadata
            )

            # Search with hypothetical answer (often better matches!)
            results_hyde = await self.vector_store.hybrid_search(
                query=hypothetical_answer,
                n_results=max_sources * 2,
                alpha=0.7,  # Higher semantic weight for HyDE
                filter_metadata=filter_metadata
            )

            # Combine and deduplicate results
            candidate_results = self._merge_search_results(results_original, results_hyde, max_sources * 3)

            # Stage 2: Add surrounding context to top candidates
            enriched_results = await self._add_surrounding_context(
                candidate_results[:max_sources * 2],
                document_id,
                context_window=1  # Include 1 chunk before/after
            )

            # Stage 3: Re-rank results
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
        """
        Generate a hypothetical answer using LLM (HyDE technique).
        This answer is used for retrieval - answers match documents better than questions.

        Args:
            question: User's question

        Returns:
            Hypothetical answer text
        """
        try:
            if not self.translator:
                return question  # Fallback to original question

            hyde_prompt = f"""Generate a brief hypothetical answer to this question. The answer should be written as if it came from a technical document or research paper. Keep it under 100 words.

Question: {question}

Hypothetical answer:"""

            # Use the translator's LLM
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
            return question  # Fallback to original question

    def _merge_search_results(self, results1: List[Dict[str, Any]],
                              results2: List[Dict[str, Any]],
                              max_results: int) -> List[Dict[str, Any]]:
        """
        Merge and deduplicate results from multiple searches.

        Args:
            results1: First set of results
            results2: Second set of results
            max_results: Maximum number to return

        Returns:
            Merged and deduplicated results
        """
        seen_ids = set()
        merged = []

        # Add all results, deduplicating by chunk_id
        for result in results1 + results2:
            chunk_id = result.get('chunk_id')
            if chunk_id and chunk_id not in seen_ids:
                seen_ids.add(chunk_id)
                merged.append(result)

        # Sort by score (prefer higher scores)
        merged.sort(key=lambda x: x.get('final_score', x.get('similarity_score', 0)), reverse=True)

        return merged[:max_results]

    async def _add_surrounding_context(self, chunks: List[Dict[str, Any]],
                                      document_id: Optional[str],
                                      context_window: int = 1) -> List[Dict[str, Any]]:
        """
        Add surrounding chunks to provide better context.

        Args:
            chunks: List of retrieved chunks
            document_id: Document ID to search within
            context_window: Number of chunks before/after to include

        Returns:
            Chunks with added surrounding context
        """
        if not document_id or not chunks:
            return chunks

        enriched_chunks = []

        for chunk in chunks:
            metadata = chunk.get('metadata', {})
            page = metadata.get('page', 0)
            chunk_index = metadata.get('chunk_index', 0)

            # Get surrounding chunks from the same page
            surrounding = await self.vector_store.get_document_chunks(
                document_id=document_id,
                page_range=(max(0, page - 1), page + 1),  # Current and nearby pages
                limit=None
            )

            # Find chunks around the current chunk
            context_before = []
            context_after = []

            for surr_chunk in surrounding:
                surr_meta = surr_chunk.get('metadata', {})
                surr_page = surr_meta.get('page', 0)
                surr_index = surr_meta.get('chunk_index', 0)

                # Same page, chunk before
                if surr_page == page and surr_index < chunk_index and chunk_index - surr_index <= context_window:
                    context_before.append(surr_chunk['text'])

                # Same page, chunk after
                if surr_page == page and surr_index > chunk_index and surr_index - chunk_index <= context_window:
                    context_after.append(surr_chunk['text'])

            # Build enriched text with context
            enriched_text_parts = []
            if context_before:
                enriched_text_parts.append("...\n" + "\n".join(context_before) + "\n")
            enriched_text_parts.append(chunk['text'])
            if context_after:
                enriched_text_parts.append("\n" + "\n".join(context_after) + "\n...")

            enriched_chunk = chunk.copy()
            enriched_chunk['text'] = "".join(enriched_text_parts)
            enriched_chunk['original_text'] = chunk['text']  # Keep original
            enriched_chunks.append(enriched_chunk)

        return enriched_chunks

    async def _rerank_results(self, question: str, chunks: List[Dict[str, Any]],
                             top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Re-rank results using multiple signals for better relevance.

        Args:
            question: User's question
            chunks: Retrieved chunks to re-rank
            top_k: Number of top results to return

        Returns:
            Re-ranked chunks
        """
        if not chunks:
            return []

        question_lower = question.lower()
        question_words = set(question_lower.split())

        # Detect if this is a metadata query (title, author, abstract, etc.)
        metadata_keywords = {
            'en': ['title', 'author', 'abstract', 'summary', 'introduction', 'conclusion'],
            'vi': ['tiêu đề', 'tác giả', 'tóm tắt', 'giới thiệu', 'kết luận']
        }
        is_metadata_query = any(
            kw in question_lower
            for keywords in metadata_keywords.values()
            for kw in keywords
        )

        # Calculate re-ranking score for each chunk
        for chunk in chunks:
            text_lower = chunk.get('original_text', chunk.get('text', '')).lower()
            metadata = chunk.get('metadata', {})
            page = metadata.get('page', 100)
            # Note: stored as 'chunk_type' in ChromaDB
            section_type = metadata.get('chunk_type', metadata.get('section_type', 'content'))
            chunk_index = metadata.get('chunk_index', 100)

            # Component scores
            base_score = chunk.get('final_score', chunk.get('similarity_score', 0))

            # Keyword density score
            words_in_text = set(text_lower.split())
            keyword_matches = len(question_words & words_in_text)
            keyword_density = keyword_matches / max(len(question_words), 1)

            # Page position score (earlier pages often more important)
            page_score = 1.0 / (1.0 + page * 0.1)  # Decay with page number

            # Chunk index score (earlier chunks more important)
            index_score = 1.0 / (1.0 + chunk_index * 0.05)

            # Length score (prefer moderate-length chunks)
            text_length = len(chunk.get('text', ''))
            length_score = min(text_length / 500, 1.0)  # Normalize to 500 chars

            # Section type score (CRITICAL for metadata queries)
            section_score = 0.0
            if is_metadata_query:
                if section_type == 'title':
                    section_score = 1.0  # Maximum boost for title chunks
                elif section_type == 'header':
                    section_score = 0.8  # High boost for headers
                elif section_type == 'abstract':
                    section_score = 0.9  # Very high for abstract
            else:
                # For content queries, prefer content sections
                if section_type == 'content':
                    section_score = 0.3

            # Combine scores with adaptive weights
            if is_metadata_query:
                # For metadata queries, heavily weight section type and position
                rerank_score = (
                    0.15 * base_score +          # Lower weight on semantic
                    0.05 * keyword_density +     # Lower weight on keywords
                    0.2 * page_score +           # Important: early pages
                    0.2 * index_score +          # Important: early chunks
                    0.1 * length_score +         # Some weight on length
                    0.3 * section_score          # CRITICAL: section type gets highest weight!
                )
            else:
                # For content queries, standard weighting
                rerank_score = (
                    0.4 * base_score +           # Original search score
                    0.3 * keyword_density +      # Keyword matching
                    0.15 * page_score +          # Page position
                    0.05 * index_score +         # Chunk position
                    0.05 * length_score +        # Content length
                    0.05 * section_score         # Section type bonus
                )

            chunk['rerank_score'] = rerank_score
            chunk['final_score'] = rerank_score  # Update final score
            chunk['is_metadata_query'] = is_metadata_query

        # Sort by rerank score
        chunks.sort(key=lambda x: x.get('rerank_score', 0), reverse=True)

        # Return top k
        return chunks[:top_k]

    def _extract_pdf_context(self, pdf_sources: List[Dict[str, Any]]) -> str:
        """Extract context from PDF sources for web research."""

        if not pdf_sources:
            return ""
        
        # Combine text from top PDF sources
        context_parts = []
        for source in pdf_sources[:3]:  # Use top 3 sources for context
            text = source.get('text', '')
            if text:
                context_parts.append(text[:200])  # Limit length
        
        return ' '.join(context_parts)

    async def _answer_with_deep_search(
        self,
        question: str,
        document_id: Optional[str],
        progress_callback: Optional[callable]
    ) -> Dict[str, Any]:
        """
        Answer question using Deep Search (multi-hop academic search).

        Args:
            question: User's question
            document_id: Specific document ID (optional)
            progress_callback: Progress callback for UI updates

        Returns:
            Result dict with answer and references
        """
        logger.info(f"Using Deep Search for: {question[:100]}")

        try:
            # Get current PDF context if document_id provided
            current_pdf_context = ""
            if document_id:
                pdf_sources = await self._retrieve_pdf_knowledge(question, document_id, 3)
                current_pdf_context = self._extract_pdf_context(pdf_sources)

            # Run deep search
            deep_result: DeepSearchResult = await self.deep_search_engine.deep_search(
                question=question,
                current_pdf_context=current_pdf_context,
                progress_callback=progress_callback
            )

            # Convert deep search papers to references
            paper_references = []
            for paper in deep_result.papers:
                ref = {
                    'title': paper.title,
                    'authors': ', '.join(paper.authors[:3]),
                    'year': paper.year,
                    'source': paper.source,
                    'abstract': paper.abstract[:200] if paper.abstract else '',
                    'url': paper.pdf_url or '',
                    'relevance': paper.relevance_score
                }
                paper_references.append(ref)

            # Convert local papers to references
            local_references = []
            for paper in deep_result.local_papers:
                ref = {
                    'title': paper.title,
                    'source': 'Local ChromaDB',
                    'relevance': paper.relevance_score
                }
                local_references.append(ref)

            result = {
                'answer': deep_result.answer,
                'pdf_references': local_references,
                'web_references': paper_references,
                'deep_search_result': deep_result.to_dict(),
                'quality_metrics': {
                    'confidence': 0.9,  # High confidence from deep search
                    'completeness': min(deep_result.total_papers / 15, 1.0),
                    'total_papers': deep_result.total_papers,
                    'total_hops': deep_result.total_hops
                },
                'processing_time': deep_result.processing_time,
                'sources_used': {
                    'pdf_sources': len(deep_result.local_papers),
                    'academic_papers': len(deep_result.papers)
                },
                'timestamp': datetime.now().isoformat(),
                'search_type': 'deep_search'
            }

            logger.info(f"Deep Search completed: {deep_result.total_papers} papers, "
                       f"{deep_result.processing_time:.1f}s")
            return result

        except Exception as e:
            logger.error(f"Deep Search failed: {e}", exc_info=True)
            # Fallback to standard RAG
            logger.info("Falling back to standard RAG")
            return await self.answer_question(
                question, document_id,
                include_web_research=True,
                use_deep_search=False
            )

    async def _generate_answer(self, question: str, pdf_sources: List[Dict[str, Any]],
                             web_sources: List[WebSource]) -> str:
        """Generate comprehensive answer using all available sources."""

        # Prepare context from all sources
        context_parts = []

        # Add PDF context
        if pdf_sources:
            context_parts.append("=== INFORMATION FROM PDF DOCUMENTS ===")
            for i, source in enumerate(pdf_sources[:3]):
                text = source.get('text', '')
                page = source.get('metadata', {}).get('page', 'N/A')
                context_parts.append(f"[PDF Source {i+1}, Page {page}]: {text[:300]}...")

        # Add web context
        if web_sources:
            context_parts.append("\n=== INFORMATION FROM INTERNET ===")
            for i, source in enumerate(web_sources[:3]):
                context_parts.append(f"[Web Source {i+1} - {source.source_type}]: {source.content[:300]}...")

        full_context = '\n'.join(context_parts)

        # Create prompt for answer generation
        prompt = self._create_answer_prompt(question, full_context, pdf_sources, web_sources)
        
        try:
            # Generate answer using the translator/LLM
            if self.translator:
                answer = await self._generate_with_llm(prompt)
            else:
                # Fallback to template-based answer
                answer = self._generate_template_answer(question, pdf_sources, web_sources)
            
            return answer
            
        except Exception as e:
            logger.error(f"Answer generation failed: {e}")
            return self._generate_template_answer(question, pdf_sources, web_sources)
    
    def _create_answer_prompt(self, question: str, context: str,
                            pdf_sources: List[Dict[str, Any]],
                            web_sources: List[WebSource]) -> str:
        """Create a comprehensive prompt for answer generation."""
        
        prompt = f"""
You are an intelligent AI assistant specialized in answering questions based on translated PDF documents and information from the internet.

TASK:
- Answer questions comprehensively and accurately
- Combine information from both PDF and internet sources
- Prioritize information from PDF (primary source)
- Supplement with internet information to clarify or expand
- Answer in Vietnamese

QUESTION: {question}

AVAILABLE INFORMATION:
{context}

ANSWER REQUIREMENTS:
Answer the question concisely, accurately, and completely. Provide only the final answer without dividing into multiple sections or detailed explanations. If information comes from the internet (not in the PDF), clearly state the source.

ANSWER:
"""
        
        return prompt
    
    async def _generate_with_llm(self, prompt: str) -> str:
        """Generate answer using LLM (OpenAI/Gemini)."""
        
        try:
            # Use the translator's LLM for text generation
            # This is a simplified approach - in production, you'd want a dedicated chat model
            
            if hasattr(self.translator, 'client'):
                # For OpenAI
                if hasattr(self.translator.client, 'chat'):
                    response = self.translator.client.chat.completions.create(
                        model=self.translator.model,
                        messages=[
                            {"role": "system", "content": "You are an intelligent AI assistant that answers questions based on documents."},
                            {"role": "user", "content": prompt}
                        ],
                        max_tokens=1000,
                        temperature=0.3
                    )
                    return response.choices[0].message.content
                
                # For Gemini
                elif hasattr(self.translator, 'model'):
                    response = self.translator.model.generate_content(prompt)
                    return response.text
            
            # Fallback
            return "Unable to generate answer with LLM."
            
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            raise
    
    def _generate_template_answer(self, question: str, pdf_sources: List[Dict[str, Any]],
                                web_sources: List[WebSource]) -> str:
        """Generate template-based answer as fallback."""
        
        answer_parts = []
        
        # Introduction
        answer_parts.append(f"Based on available information, I will answer the question: '{question}'")
        
        # PDF information
        if pdf_sources:
            answer_parts.append("\n**Information from PDF documents:**")
            for i, source in enumerate(pdf_sources[:2]):
                text = source.get('text', '')
                page = source.get('metadata', {}).get('page', 'N/A')
                answer_parts.append(f"- Page {page}: {text[:200]}...")

        # Web information
        if web_sources:
            answer_parts.append("\n**Additional information from internet:**")
            for i, source in enumerate(web_sources[:2]):
                answer_parts.append(f"- {source.source_type.title()}: {source.snippet}")

        # Conclusion
        if pdf_sources or web_sources:
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
            
            # Add bounding box if available (for highlighting)
            if 'elements' in metadata:
                try:
                    elements = json.loads(metadata['elements'])
                    if elements and len(elements) > 0:
                        first_element = elements[0]
                        if 'bbox' in first_element:
                            reference['bbox'] = first_element['bbox']
                except:
                    pass
            
            references.append(reference)
        
        return references
    
    def _create_web_references(self, web_sources: List[WebSource]) -> List[Dict[str, Any]]:
        """Create web references with source information."""
        
        references = []
        
        for source in web_sources:
            reference = {
                'type': 'web',
                'url': source.url,
                'title': source.title,
                'snippet': source.snippet,
                'source_type': source.source_type,
                'reliability_score': source.reliability_score,
                'scraped_at': source.scraped_at.isoformat()
            }
            references.append(reference)
        
        return references
    
    def _calculate_quality_metrics(self, pdf_sources: List[Dict[str, Any]],
                                 web_sources: List[WebSource]) -> Dict[str, float]:
        """Calculate quality metrics for the answer."""

        # Simple metrics without confidence calculation
        return {
            'total_sources': len(pdf_sources) + len(web_sources)
        }
    
