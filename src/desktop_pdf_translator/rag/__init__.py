"""
RAG (Retrieval-Augmented Generation) module for PDFusion.

Provides Q&A over translated PDFs using ChromaDB-backed retrieval and LLM synthesis.

Features:
- Scientific PDF processing with layout preservation
- Multi-modal embeddings (text, equations, tables, figures)
- Reference system with page navigation
- Vietnamese language optimization
"""

from .document_processor import ScientificPDFProcessor
from .vector_store import ChromaDBManager
from .rag_chain import EnhancedRAGChain
from .reference_manager import ReferenceManager

__all__ = [
    'ScientificPDFProcessor',
    'ChromaDBManager',
    'EnhancedRAGChain',
    'ReferenceManager'
]

__version__ = "1.0.0"
