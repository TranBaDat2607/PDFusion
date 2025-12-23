"""
LangGraph tools for RAG agent system.
Simple, clean tool definitions using @tool decorator.
"""

import logging
from typing import Optional, Dict, Any
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Global references to existing components (set during initialization)
_rag_chain = None
_web_research = None
_deep_search = None


def initialize_tools(rag_chain, web_research, deep_search):
    """
    Initialize tools with existing RAG components.

    Args:
        rag_chain: EnhancedRAGChain instance
        web_research: WebResearchEngine instance
        deep_search: DeepSearchEngine instance
    """
    global _rag_chain, _web_research, _deep_search
    _rag_chain = rag_chain
    _web_research = web_research
    _deep_search = deep_search
    logger.info("[TOOLS] Initialized with existing RAG components")


@tool
async def rag_tool(query: str, document_id: Optional[str] = None, max_sources: int = 5) -> Dict[str, Any]:
    """
    Search local PDF documents using vector similarity.

    Use this tool when:
    - Question is about the current PDF document
    - Need to find specific information in the document
    - Want to check if PDF has relevant information

    Args:
        query: Search query or question
        document_id: Specific document ID (optional)
        max_sources: Maximum sources to retrieve (default: 5)

    Returns:
        Dictionary with PDF sources and metadata
    """
    try:
        logger.info(f"[RAG_TOOL] Searching PDF: {query[:100]}...")

        if not _rag_chain:
            return {"error": "RAG chain not initialized", "sources": [], "num_sources": 0}

        # Use existing RAG chain retrieval
        sources = await _rag_chain._retrieve_pdf_knowledge(
            question=query,
            document_id=document_id,
            max_sources=max_sources
        )

        # Calculate quality metrics
        avg_score = 0.0
        if sources:
            scores = [s.get('rerank_score', 0.0) or s.get('score', 0.0) for s in sources]
            avg_score = sum(scores) / len(scores)

        # Evaluate sufficiency
        is_sufficient = len(sources) >= 3 and avg_score >= 0.6

        result = {
            "sources": sources,
            "num_sources": len(sources),
            "avg_score": round(avg_score, 2),
            "is_sufficient": is_sufficient,
            "preview": sources[0]['text'][:200] if sources else None
        }

        logger.info(f"[RAG_TOOL] Found {len(sources)} sources, avg_score: {avg_score:.2f}, sufficient: {is_sufficient}")
        return result

    except Exception as e:
        logger.error(f"[RAG_TOOL] Error: {e}")
        return {"error": str(e), "sources": [], "num_sources": 0}


@tool
async def web_search_tool(query: str, num_results: int = 5, pdf_context: str = "") -> Dict[str, Any]:
    """
    Search the web for general knowledge, facts, and definitions.

    Use this tool when:
    - Need general knowledge or context
    - Looking for definitions or explanations
    - PDF doesn't have sufficient information
    - Need current/external information

    Performance: Fast (~3-5 seconds)

    Args:
        query: Web search query
        num_results: Number of results to retrieve (default: 5)
        pdf_context: Optional PDF context to enhance search

    Returns:
        Dictionary with web sources and metadata
    """
    try:
        logger.info(f"[WEB_TOOL] Searching web: {query[:100]}...")

        if not _web_research:
            return {"error": "Web research engine not initialized", "sources": [], "num_sources": 0}

        # Use existing web research engine
        web_sources = await _web_research.research_topic(
            query=query,
            pdf_context=pdf_context
        )

        # Limit and format results
        web_sources = web_sources[:num_results]
        formatted_sources = [
            {
                'url': source.url,
                'title': source.title,
                'content': source.content[:500],  # Truncate for agent context
                'source_type': source.source_type,
                'reliability': round(source.reliability_score, 2)
            }
            for source in web_sources
        ]

        result = {
            "sources": formatted_sources,
            "num_sources": len(formatted_sources),
            "source_types": list(set(s['source_type'] for s in formatted_sources)),
            "avg_reliability": round(sum(s['reliability'] for s in formatted_sources) / len(formatted_sources), 2) if formatted_sources else 0.0
        }

        logger.info(f"[WEB_TOOL] Found {len(formatted_sources)} sources")
        return result

    except Exception as e:
        logger.error(f"[WEB_TOOL] Error: {e}")
        return {"error": str(e), "sources": [], "num_sources": 0}


@tool
async def academic_search_tool(
    query: str,
    max_hops: int = 3,
    max_papers: int = 20,
    pdf_context: str = ""
) -> Dict[str, Any]:
    """
    Deep search across academic papers with multi-hop citation following.

    Use this tool when:
    - Question requires academic/research depth
    - Need authoritative peer-reviewed sources
    - Looking for state-of-the-art research
    - Researching specific scientific topics

    WARNING: SLOW operation (~20-40 seconds)

    Args:
        query: Research question
        max_hops: Maximum citation hops (1-5, default: 3)
        max_papers: Maximum total papers (default: 20)
        pdf_context: Optional PDF context for grounding

    Returns:
        Dictionary with academic papers and metadata
    """
    try:
        logger.info(f"[ACADEMIC_TOOL] Deep search: {query[:100]}... (hops: {max_hops})")

        if not _deep_search:
            return {"error": "Deep search engine not initialized", "papers": [], "total_papers": 0}

        # Use existing deep search engine
        result = await _deep_search.deep_search(
            question=query,
            current_pdf_context=pdf_context,
            max_hops=max_hops,
            max_papers_per_hop=max_papers // max_hops if max_hops > 0 else max_papers
        )

        # Format papers for agent
        formatted_papers = [
            {
                'title': paper.title,
                'authors': ', '.join(paper.authors[:3]),
                'year': paper.year,
                'abstract': paper.abstract[:300],  # Truncate for agent context
                'source': paper.source,
                'citation_count': paper.citation_count,
                'relevance': round(paper.relevance_score, 2)
            }
            for paper in result.papers
        ]

        response = {
            "papers": formatted_papers,
            "total_papers": result.total_papers,
            "total_hops": result.total_hops,
            "answer_summary": result.answer[:500] if result.answer else None
        }

        logger.info(f"[ACADEMIC_TOOL] Found {result.total_papers} papers across {result.total_hops} hops")
        return response

    except Exception as e:
        logger.error(f"[ACADEMIC_TOOL] Error: {e}")
        return {"error": str(e), "papers": [], "total_papers": 0}


def get_tools():
    """
    Get all available tools for LangGraph agent.

    Returns:
        List of LangChain tools
    """
    return [rag_tool, web_search_tool, academic_search_tool]
