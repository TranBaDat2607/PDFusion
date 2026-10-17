"""What a chat index is built with — recorded on every index (#59).

A document's ready index is looked up by these values (`storage/records.py`),
so changing one makes every existing index stale: documents are indexed again
the next time chat opens them, instead of mixing vectors from two embedding
models, or chunks from two chunkers, in one index.

Stdlib-only: `api/routes/rag.py` imports it at module level, on the boot path.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingSpec:
    """How to run one ONNX embedding model the way it was trained to be run.

    Everything here is from the model's card and its sentence-transformers
    config; getting any of it wrong gives vectors that are quietly worse, with
    no error to say so.
    """

    repo: str
    onnx_file: str
    dimensions: int
    # Longer inputs are truncated.
    max_tokens: int
    # "mean" over the non-padding tokens, or "cls": the first token.
    pooling: str = "mean"
    query_prefix: str = ""
    passage_prefix: str = ""


EMBEDDING_SPECS = {
    spec.repo: spec
    for spec in (
        EmbeddingSpec(
            repo="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            onnx_file="onnx/model.onnx",
            dimensions=384,
            max_tokens=128,
        ),
        # The candidates `benchmarks/rag/` measured against each other.
        EmbeddingSpec(
            repo="intfloat/multilingual-e5-small",
            onnx_file="onnx/model.onnx",
            dimensions=384,
            max_tokens=512,
            query_prefix="query: ",
            passage_prefix="passage: ",
        ),
        EmbeddingSpec(
            repo="Xenova/multilingual-e5-small",
            onnx_file="onnx/model_quantized.onnx",
            dimensions=384,
            max_tokens=512,
            query_prefix="query: ",
            passage_prefix="passage: ",
        ),
        EmbeddingSpec(
            repo="Xenova/multilingual-e5-base",
            onnx_file="onnx/model_quantized.onnx",
            dimensions=768,
            max_tokens=512,
            query_prefix="query: ",
            passage_prefix="passage: ",
        ),
        EmbeddingSpec(
            repo="Xenova/bge-m3",
            onnx_file="onnx/model_quantized.onnx",
            dimensions=1024,
            max_tokens=1024,
            pooling="cls",
        ),
    )
}

# multilingual-e5-small, int8: measured against MiniLM (the model before it),
# e5-small in fp32, e5-base and bge-m3 on `benchmarks/rag/`. Ahead of MiniLM on
# every retrieval measure (99 ranked questions: hit@1 0.82 → 0.87, hit@3 0.95 →
# 0.98) though not significantly (McNemar p 0.23) — chosen as at least as good,
# at MiniLM's speed, for a 118 MB download instead of 470 MB. bge-m3 was
# marginally better still and five times slower to index on a CPU.
EMBEDDING_MODEL = "Xenova/multilingual-e5-small"
EMBEDDING_DIMENSIONS = EMBEDDING_SPECS[EMBEDDING_MODEL].dimensions

# Bump when `rag/ingest.py` changes how a PDF becomes chunks, or
# `vector_store.add_chunks` what is embedded for one. "2": page-aware ingestion
# with OCR, embedded under section headings, replaced ScientificPDFProcessor.
# "3": chunks carry the subsection in force (`subheading`) for the outline.
CHUNKER_VERSION = "3"
