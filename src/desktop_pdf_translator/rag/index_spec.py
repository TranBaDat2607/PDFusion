"""What a chat index is built with — recorded on every index (#59).

A document's ready index is looked up by these values (`storage/records.py`),
so changing one makes every existing index stale: documents are indexed again
the next time chat opens them, instead of mixing vectors from two embedding
models, or chunks from two chunkers, in one index.

Stdlib-only: `api/routes/rag.py` imports it at module level, on the boot path.
"""

EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIMENSIONS = 384

# Bump when `rag/document_processor.py` changes how a PDF becomes chunks.
CHUNKER_VERSION = "1"
