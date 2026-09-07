"""RAG (Retrieval-Augmented Generation) module for PDFusion.

Q&A over translated PDFs using ChromaDB-backed retrieval and LLM synthesis,
with scientific-PDF layout preservation, multi-modal embeddings and
page-anchored references.

Nothing is re-exported: every member of this package reaches torch, chromadb or
camelot, and RAG is off by default, so the sidecar must be able to boot without
paying for any of it. Import from the submodule at the call site.
"""

__version__ = "1.0.6"
