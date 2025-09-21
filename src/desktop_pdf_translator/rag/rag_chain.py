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
    
    async def answer_question(self, question: str, document_id: Optional[str] = None,
                            include_web_research: bool = True,
                            max_pdf_sources: int = 5,
                            max_web_sources: int = 5) -> Dict[str, Any]:
        """
        Answer a question using PDF knowledge and web research.
        
        Args:
            question: User's question
            document_id: Specific document to search (optional)
            include_web_research: Whether to include web research
            max_pdf_sources: Maximum PDF sources to retrieve
            max_web_sources: Maximum web sources to retrieve
            
        Returns:
            Comprehensive answer with references
        """
        logger.info(f"Processing question: {question[:100]}...")
        
        start_time = datetime.now()
        
        try:
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
                'answer': f"Xin lỗi, tôi không thể trả lời câu hỏi này do lỗi: {str(e)}",
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
                # Search within specific document
                filter_metadata = {"document_id": document_id}
            else:
                # Search across all documents
                filter_metadata = None
            
            # Use hybrid search for better results
            results = await self.vector_store.hybrid_search(
                query=question,
                n_results=max_sources,
                alpha=0.7  # Weight semantic search higher
            )
            
            logger.info(f"Retrieved {len(results)} PDF sources")
            return results
            
        except Exception as e:
            logger.error(f"PDF knowledge retrieval failed: {e}")
            return []
    
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
    
    async def _generate_answer(self, question: str, pdf_sources: List[Dict[str, Any]],
                             web_sources: List[WebSource]) -> str:
        """Generate comprehensive answer using all available sources."""
        
        # Prepare context from all sources
        context_parts = []
        
        # Add PDF context
        if pdf_sources:
            context_parts.append("=== THÔNG TIN TỪ TÀI LIỆU PDF ===")
            for i, source in enumerate(pdf_sources[:3]):
                text = source.get('text', '')
                page = source.get('metadata', {}).get('page', 'N/A')
                context_parts.append(f"[Nguồn PDF {i+1}, Trang {page}]: {text[:300]}...")
        
        # Add web context
        if web_sources:
            context_parts.append("\n=== THÔNG TIN TỪ INTERNET ===")
            for i, source in enumerate(web_sources[:3]):
                context_parts.append(f"[Nguồn Web {i+1} - {source.source_type}]: {source.content[:300]}...")
        
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
Bạn là một trợ lý AI thông minh, chuyên trả lời câu hỏi dựa trên tài liệu PDF đã dịch và thông tin từ internet.

NHIỆM VỤ:
- Trả lời câu hỏi một cách toàn diện và chính xác
- Kết hợp thông tin từ cả PDF và internet
- Ưu tiên thông tin từ PDF (nguồn chính)
- Bổ sung thông tin từ internet để làm rõ hoặc mở rộng
- Trả lời bằng tiếng Việt

CÂU HỎI: {question}

THÔNG TIN CÓ SẴN:
{context}

YÊU CẦU TRẢ LỜI:
1. Trả lời trực tiếp câu hỏi
2. Giải thích chi tiết dựa trên nguồn thông tin
3. Nêu rõ khi thông tin đến từ PDF vs internet
4. Đưa ra kết luận hoặc tóm tắt
5. Sử dụng ngôn ngữ rõ ràng, dễ hiểu

TRẢ LỜI:
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
                            {"role": "system", "content": "Bạn là trợ lý AI thông minh, trả lời câu hỏi dựa trên tài liệu."},
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
            return "Không thể tạo câu trả lời với LLM."
            
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            raise
    
    def _generate_template_answer(self, question: str, pdf_sources: List[Dict[str, Any]],
                                web_sources: List[WebSource]) -> str:
        """Generate template-based answer as fallback."""
        
        answer_parts = []
        
        # Introduction
        answer_parts.append(f"Dựa trên thông tin có sẵn, tôi sẽ trả lời câu hỏi: '{question}'")
        
        # PDF information
        if pdf_sources:
            answer_parts.append("\n**Thông tin từ tài liệu PDF:**")
            for i, source in enumerate(pdf_sources[:2]):
                text = source.get('text', '')
                page = source.get('metadata', {}).get('page', 'N/A')
                answer_parts.append(f"- Trang {page}: {text[:200]}...")
        
        # Web information
        if web_sources:
            answer_parts.append("\n**Thông tin bổ sung từ internet:**")
            for i, source in enumerate(web_sources[:2]):
                answer_parts.append(f"- {source.source_type.title()}: {source.snippet}")
        
        # Conclusion
        if pdf_sources or web_sources:
            answer_parts.append("\n**Kết luận:** Thông tin trên cung cấp cái nhìn tổng quan về câu hỏi của bạn. Để có thông tin chi tiết hơn, vui lòng tham khảo các nguồn được trích dẫn.")
        else:
            answer_parts.append("\nXin lỗi, tôi không tìm thấy thông tin liên quan để trả lời câu hỏi này.")
        
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
        
        # Confidence based on source quality and quantity
        pdf_confidence = 0.0
        if pdf_sources:
            pdf_scores = [s.get('similarity_score', 0.0) for s in pdf_sources]
            pdf_confidence = sum(pdf_scores) / len(pdf_scores)
        
        web_confidence = 0.0
        if web_sources:
            web_scores = [s.reliability_score for s in web_sources]
            web_confidence = sum(web_scores) / len(web_scores)
        
        # Overall confidence (weighted towards PDF sources)
        if pdf_sources and web_sources:
            overall_confidence = 0.7 * pdf_confidence + 0.3 * web_confidence
        elif pdf_sources:
            overall_confidence = pdf_confidence
        elif web_sources:
            overall_confidence = web_confidence * 0.6  # Lower confidence for web-only
        else:
            overall_confidence = 0.0
        
        # Completeness based on source diversity
        completeness = 0.0
        if pdf_sources:
            completeness += 0.6
        if web_sources:
            completeness += 0.4
        
        return {
            'confidence': min(overall_confidence, 1.0),
            'completeness': min(completeness, 1.0),
            'pdf_confidence': pdf_confidence,
            'web_confidence': web_confidence,
            'total_sources': len(pdf_sources) + len(web_sources)
        }
    
    async def summarize_document(self, document_id: str) -> Dict[str, Any]:
        """Generate a summary of a specific document."""
        
        try:
            # Get all chunks for the document
            chunks = await self.vector_store.search_by_document(document_id)
            
            if not chunks:
                return {'summary': 'Không tìm thấy tài liệu.', 'error': 'Document not found'}
            
            # Extract key information
            total_pages = max(chunk['metadata'].get('page', 0) for chunk in chunks) + 1
            has_equations = any(chunk['metadata'].get('has_equations', False) for chunk in chunks)
            has_tables = any(chunk['metadata'].get('has_tables', False) for chunk in chunks)
            has_figures = any(chunk['metadata'].get('has_figures', False) for chunk in chunks)
            
            # Create summary text
            content_parts = []
            for chunk in chunks[:5]:  # Use first 5 chunks for summary
                content_parts.append(chunk['text'][:200])
            
            summary_content = ' '.join(content_parts)
            
            # Generate structured summary
            summary = f"""
**Tóm tắt tài liệu:**

**Thông tin cơ bản:**
- Tổng số trang: {total_pages}
- Có công thức toán học: {'Có' if has_equations else 'Không'}
- Có bảng biểu: {'Có' if has_tables else 'Không'}
- Có hình ảnh/đồ thị: {'Có' if has_figures else 'Không'}

**Nội dung chính:**
{summary_content[:500]}...

**Các chủ đề chính:** (Được trích xuất từ nội dung tài liệu)
"""
            
            return {
                'summary': summary,
                'document_stats': {
                    'total_pages': total_pages,
                    'total_chunks': len(chunks),
                    'has_equations': has_equations,
                    'has_tables': has_tables,
                    'has_figures': has_figures
                },
                'document_id': document_id
            }
            
        except Exception as e:
            logger.error(f"Document summarization failed: {e}")
            return {'summary': f'Lỗi khi tóm tắt tài liệu: {str(e)}', 'error': str(e)}
