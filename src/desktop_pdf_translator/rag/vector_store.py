"""
Chat index storage: one ChromaDB collection per index.

Which indexes exist — for which document, built with which embedding model and
chunker — is recorded in `storage/records.py`. This module stores and searches
the chunks of one index at a time. Every method names an index, and a query only
ever reaches that index's collection, so no code path can search across
documents (#59). Collections are derived data: `api/routes/rag.py:_recover`
drops any the records don't account for.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import chromadb
from chromadb.config import Settings
from chromadb.errors import NotFoundError

from ..utils.paths import appdata_dir
from .errors import IndexUnavailableError
from .keyword_search import Bm25Index
from .onnx_embeddings import OnnxEmbeddingFunction

logger = logging.getLogger(__name__)

_COLLECTION_PREFIX = "rag_"
# Chunks per `collection.add` call: keeps a long PDF under ChromaDB's batch
# limit, and the embedding model's working memory small.
_ADD_BATCH = 256


def collection_name(index_id: str) -> str:
    return f"{_COLLECTION_PREFIX}{index_id}"


def _first_bbox(elements: Any) -> Optional[List[float]]:
    """The first element's bounding box — the only part of a chunk's element
    list anything reads (`rag_chain._create_pdf_references`)."""
    if not elements or not isinstance(elements[0], dict):
        return None
    bbox = elements[0].get('bbox')
    return [float(v) for v in bbox] if bbox else None


class ChromaDBManager:
    """ChromaDB storage for chat indexes, persisted under the app's data root."""

    def __init__(self, persist_directory: Optional[Path] = None,
                 embedding_function=None):
        """
        Args:
            persist_directory: Where ChromaDB keeps its files. Defaults to
                `vectors/` under the app's data root.
            embedding_function: ChromaDB embedding function. Defaults to the
                ONNX MiniLM model; tests pass a deterministic one so they need
                no model download.
        """
        if persist_directory is None:
            persist_directory = appdata_dir() / "vectors"

        self.persist_directory = Path(persist_directory)
        self.persist_directory.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(
            path=str(self.persist_directory),
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=True,
                is_persistent=True,
            ),
        )
        if embedding_function is None:
            embedding_function = OnnxEmbeddingFunction()
        self.embedding_function = embedding_function

        logger.info(f"ChromaDB initialized at: {self.persist_directory}")

    def _collection(self, index_id: str, create: bool = False):
        if create:
            return self.client.get_or_create_collection(
                name=collection_name(index_id),
                embedding_function=self.embedding_function,
                configuration={"hnsw": {"space": "cosine"}},
            )
        return self.client.get_collection(
            name=collection_name(index_id),
            embedding_function=self.embedding_function,
        )

    async def add_chunks(self, index_id: str, chunks: List[Dict[str, Any]]) -> int:
        """Embed and store an index's chunks; return how many were stored.

        Raises on failure. The caller fails the index and drops its collection,
        so a half-written index is never taken for a finished one.

        Chunks keep only the metadata retrieval reads. The document's id and
        path are not repeated on every chunk: the collection is the document's,
        and the records know where it lives.
        """
        ids: List[str] = []
        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        for i, chunk in enumerate(chunks):
            section = chunk.get('metadata', {})
            metadata = {
                'chunk_index': i,
                'page': chunk.get('page', 0),
                'chunk_type': section.get('section_type', 'content'),
                'has_equations': bool(section.get('has_equations', False)),
                'has_tables': bool(section.get('has_tables', False)),
                'has_figures': bool(section.get('has_figures', False)),
            }
            bbox = _first_bbox(chunk.get('elements'))
            if bbox is not None:
                metadata['bbox'] = json.dumps(bbox)
            ids.append(f"chunk_{i}")
            documents.append(chunk['text'])
            metadatas.append(metadata)

        def add() -> None:
            collection = self._collection(index_id, create=True)
            for start in range(0, len(ids), _ADD_BATCH):
                end = start + _ADD_BATCH
                collection.add(
                    ids=ids[start:end],
                    documents=documents[start:end],
                    metadatas=metadatas[start:end],
                )

        # Embedding is CPU-heavy and blocking: off the event loop.
        await asyncio.to_thread(add)
        logger.info(f"Added {len(ids)} chunks to index {index_id}")
        return len(ids)

    async def get_chunks(self, index_id: str) -> List[Dict[str, Any]]:
        """Every chunk of an index, in reading order.

        The one full read a question makes; the keyword pass and the
        surrounding-context step both work from the list this returns. Raises
        `IndexUnavailableError` when the index has no collection.
        """
        try:
            results = await asyncio.to_thread(
                lambda: self._collection(index_id).get(include=['documents', 'metadatas'])
            )
        except NotFoundError as e:
            raise IndexUnavailableError(index_id) from e
        chunks = [
            {'text': text, 'metadata': metadata, 'chunk_id': chunk_id}
            for chunk_id, text, metadata in zip(
                results['ids'], results['documents'], results['metadatas']
            )
        ]
        chunks.sort(key=lambda c: (
            c['metadata'].get('page', 0),
            c['metadata'].get('chunk_index', 0),
        ))
        return chunks

    async def search_similar(self, index_id: str, query: str,
                             n_results: int = 5) -> List[Dict[str, Any]]:
        """Semantic search within one index.

        Raises on failure: `IndexUnavailableError` when the index has no
        collection, anything else as ChromaDB raised it. An empty result used to
        stand in for a failure, and chat then answered as if the document had
        nothing on the question (#31).
        """

        def search():
            collection = self._collection(index_id)
            available = collection.count()
            if available == 0:
                return None
            return collection.query(
                query_texts=[query],
                n_results=min(n_results, available),
                include=['documents', 'metadatas', 'distances'],
            )

        try:
            # Embeds the query: off the event loop.
            results = await asyncio.to_thread(search)
        except NotFoundError as e:
            raise IndexUnavailableError(index_id) from e

        if not results or not results['documents']:
            return []

        formatted_results = [
            {
                'text': results['documents'][0][i],
                'metadata': results['metadatas'][0][i],
                'similarity_score': 1 - results['distances'][0][i],  # Convert distance to similarity
                'chunk_id': results['ids'][0][i],
            }
            for i in range(len(results['documents'][0]))
        ]
        logger.info(f"Found {len(formatted_results)} similar chunks for query: {query[:50]}...")
        return formatted_results

    async def hybrid_search(self, index_id: str, query: str,
                            chunks: List[Dict[str, Any]], keywords: Bm25Index,
                            n_results: int = 5,
                            alpha: float = 0.7) -> List[Dict[str, Any]]:
        """
        Blend semantic search with BM25 keyword scores, within one index.

        Args:
            index_id: The index to search
            query: Search query
            chunks: The index's chunks, as `get_chunks` returned them — read once
                by the caller, not once per search
            keywords: BM25 statistics built from `chunks`, in the same order
            n_results: Number of results to return
            alpha: Weight for semantic search (1-alpha for keyword search)

        Returns:
            List of ranked results
        """
        semantic_results = await self.search_similar(index_id, query, n_results * 2)

        # Scoring every chunk is CPU work, so it runs off the event loop. It
        # used to be a substring count, on the loop (#31). Only the best keyword
        # hits join the blend, as only the nearest chunks do on the semantic side.
        keyword_scores = await asyncio.to_thread(keywords.scores, query)
        best_keyword_hits = sorted(
            (i for i, score in enumerate(keyword_scores) if score > 0),
            key=lambda i: keyword_scores[i],
            reverse=True,
        )[:n_results * 2]
        keyword_results = [
            {**chunks[i], 'keyword_score': keyword_scores[i]} for i in best_keyword_hits
        ]

        # Combine and rank results
        combined_results: Dict[str, Dict[str, Any]] = {}
        for result in semantic_results:
            combined_results[result['chunk_id']] = {
                **result, 'final_score': alpha * result['similarity_score']
            }
        for result in keyword_results:
            existing = combined_results.get(result['chunk_id'])
            if existing is not None:
                existing['final_score'] += (1 - alpha) * result['keyword_score']
            else:
                combined_results[result['chunk_id']] = {
                    **result,
                    'similarity_score': 0,
                    'final_score': (1 - alpha) * result['keyword_score'],
                }

        final_results = sorted(
            combined_results.values(), key=lambda x: x['final_score'], reverse=True
        )
        return final_results[:n_results]

    def has_index(self, index_id: str) -> bool:
        """Whether an index has a collection. Blocking."""
        try:
            self._collection(index_id)
        except NotFoundError:
            return False
        return True

    def drop_index(self, index_id: str) -> bool:
        """Delete an index's collection; `False` if it had none.

        Blocking: from async code, call it through `asyncio.to_thread`.
        """
        try:
            self.client.delete_collection(collection_name(index_id))
        except NotFoundError:
            return False
        logger.info(f"Dropped the collection of index {index_id}")
        return True

    def index_ids(self) -> Set[str]:
        """The ids of every index that has a collection. Blocking."""
        return {
            collection.name[len(_COLLECTION_PREFIX):]
            for collection in self.client.list_collections()
            if collection.name.startswith(_COLLECTION_PREFIX)
        }
