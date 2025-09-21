"""
RAG (Retrieval-Augmented Generation) module for PDFusion.

This module provides intelligent Q&A capabilities for translated PDFs,
combining document knowledge with web research for comprehensive answers.

Features:
- Scientific PDF processing with layout preservation
- Web research integration (Google, Scholar, arXiv)
- Multi-modal embeddings (text, equations, tables, figures)
- Reference system with page navigation
- Vietnamese language optimization
"""

from .document_processor import ScientificPDFProcessor
from .vector_store import ChromaDBManager
from .web_research import WebResearchEngine
from .rag_chain import EnhancedRAGChain
from .reference_manager import ReferenceManager

__all__ = [
    'ScientificPDFProcessor',
    'ChromaDBManager', 
    'WebResearchEngine',
    'EnhancedRAGChain',
    'ReferenceManager'
]

__version__ = "1.0.0"
