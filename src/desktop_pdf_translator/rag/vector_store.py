"""
Vector store manager using ChromaDB for efficient similarity search.
Handles embeddings, indexing, and retrieval for RAG system.
"""

import logging
import asyncio
import os
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import json
import hashlib
from datetime import datetime

# Disable ChromaDB telemetry to prevent errors
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_SERVER_NOFILE"] = "1"
os.environ["CHROMA_CLIENT_AUTH_PROVIDER"] = ""
os.environ["CHROMA_CLIENT_AUTH_CREDENTIALS"] = ""

# Additional telemetry disabling for newer versions
os.environ["CHROMA_TELEMETRY_DISABLED"] = "True"
os.environ["CHROMA_TELEMETRY"] = "False"

# Disable posthog specifically
try:
    import posthog
    posthog.disabled = True
except:
    pass

# Additional telemetry disabling
try:
    import chromadb
    if hasattr(chromadb, 'telemetry'):
        chromadb.telemetry.disable()
except:
    pass

try:
    import chromadb
    from chromadb.config import Settings
    from chromadb import EmbeddingFunction, Embeddings
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    logging.error("ChromaDB not available - install with: pip install chromadb")

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    logging.error("SentenceTransformers not available - install with: pip install sentence-transformers")

logger = logging.getLogger(__name__)


class CustomEmbeddingFunction(EmbeddingFunction):
    """Custom embedding function that follows ChromaDB interface."""
    
    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        self.model_name = model_name
        self.model = None
        self._load_model()
    
    def _load_model(self):
        """Load the embedding model."""
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            raise ImportError("SentenceTransformers not available")
        
        try:
            self.model = SentenceTransformer(self.model_name)
            logger.info(f"Loaded embedding model: {self.model_name}")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            # Fallback to smaller model
            try:
                self.model = SentenceTransformer("all-MiniLM-L6-v2")
                logger.info("Loaded fallback embedding model: all-MiniLM-L6-v2")
            except Exception as e2:
                logger.error(f"Failed to load fallback model: {e2}")
                raise
    
    def __call__(self, input: List[str]) -> Embeddings:
        """
        Encode texts into embeddings following ChromaDB interface.
        
        Args:
            input: List of text strings to encode
            
        Returns:
            List of embedding vectors
        """
        if not self.model:
            raise RuntimeError("Embedding model not loaded")
        
        try:
            embeddings = self.model.encode(input, convert_to_numpy=True)
            return embeddings.tolist()
        except Exception as e:
            logger.error(f"Text encoding failed: {e}")
            raise


class EmbeddingManager:
    """Manages different types of embeddings for multi-modal content."""
    
    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        """
        Initialize embedding manager.
        
        Args:
            model_name: Name of the sentence transformer model
        """
        self.model_name = model_name
        self.model = None
        self._load_model()
    
    def _load_model(self):
        """Load the embedding model."""
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            raise ImportError("SentenceTransformers not available")
        
        try:
            self.model = SentenceTransformer(self.model_name)
            logger.info(f"Loaded embedding model: {self.model_name}")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            # Fallback to smaller model
            try:
                self.model = SentenceTransformer("all-MiniLM-L6-v2")
                logger.info("Loaded fallback embedding model: all-MiniLM-L6-v2")
            except Exception as e2:
                logger.error(f"Failed to load fallback model: {e2}")
                raise
    
    def encode_text(self, texts: List[str]) -> List[List[float]]:
        """
        Encode texts into embeddings.
        
        Args:
            texts: List of text strings to encode
            
        Returns:
            List of embedding vectors
        """
        if not self.model:
            raise RuntimeError("Embedding model not loaded")
        
        try:
            embeddings = self.model.encode(texts, convert_to_numpy=True)
            return embeddings.tolist()
        except Exception as e:
            logger.error(f"Text encoding failed: {e}")
            raise
    
    def encode_single(self, text: str) -> List[float]:
        """Encode single text into embedding."""
        return self.encode_text([text])[0]


class ChromaDBManager:
    """
    ChromaDB manager for vector storage and retrieval.
    Optimized for desktop applications with local persistence.
    """
    
    def __init__(self, persist_directory: Optional[Path] = None):
        """
        Initialize ChromaDB manager.
        
        Args:
            persist_directory: Directory to persist the database
        """
        if not CHROMADB_AVAILABLE:
            raise ImportError("ChromaDB not available")
        
        # Set default persist directory
        if persist_directory is None:
            persist_directory = Path.home() / "AppData" / "Local" / "PDFusion" / "chroma_db"
        
        self.persist_directory = Path(persist_directory)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        
        # Initialize ChromaDB client with proper telemetry settings
        try:
            # Try with comprehensive settings first
            self.client = chromadb.PersistentClient(
                path=str(self.persist_directory),
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=True,
                    is_persistent=True,
                    chroma_server_nofile=False  # Explicitly set to False for Windows
                )
            )
            logger.info("ChromaDB initialized with full settings")
        except Exception as e:
            # Fallback with minimal settings if there are issues
            logger.warning(f"Failed to initialize ChromaDB with full settings: {e}")
            try:
                self.client = chromadb.PersistentClient(
                    path=str(self.persist_directory),
                    settings=Settings(anonymized_telemetry=False)
                )
                logger.info("ChromaDB initialized with minimal settings")
            except Exception as e2:
                # Last resort - basic initialization
                logger.warning(f"Failed with minimal settings, using basic initialization: {e2}")
                self.client = chromadb.PersistentClient(path=str(self.persist_directory))
        
        # Initialize embedding function for ChromaDB
        self.embedding_function = CustomEmbeddingFunction()
        
        # Initialize embedding manager (for backward compatibility)
        self.embedding_manager = EmbeddingManager()
        
        # Collection for PDF documents
        self.collection = None
        self._initialize_collection()
        
        logger.info(f"ChromaDB initialized at: {self.persist_directory}")
    
    def _initialize_collection(self):
        """Initialize or get existing collection."""
        collection_name = "pdf_documents"
        
        try:
            # Try to get existing collection first
            self.collection = self.client.get_collection(
                name=collection_name,
                embedding_function=self.embedding_function
            )
            logger.info(f"Loaded existing collection: {collection_name}")
        except ValueError as e:
            if "does not exist" in str(e).lower():
                # Collection doesn't exist, create new one
                try:
                    self.collection = self.client.create_collection(
                        name=collection_name,
                        embedding_function=self.embedding_function,
                        metadata={"hnsw:space": "cosine"}
                    )
                    logger.info(f"Created new collection: {collection_name}")
                except ValueError as create_error:
                    if "already exists" in str(create_error).lower():
                        # Collection was created by another process, get it
                        self.collection = self.client.get_collection(
                            name=collection_name,
                            embedding_function=self.embedding_function
                        )
                        logger.info(f"Retrieved existing collection: {collection_name}")
                    else:
                        raise create_error
            else:
                raise e
        except Exception as e:
            logger.error(f"Failed to initialize collection: {e}")
            raise
    
    async def add_document_chunks(self, chunks: List[Dict[str, Any]], 
                                document_id: str, document_path: str) -> bool:
        """
        Add document chunks to vector store.
        
        Args:
            chunks: List of document chunks with text and metadata
            document_id: Unique identifier for the document
            document_path: Path to the original document
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Prepare data for ChromaDB
            ids = []
            documents = []
            metadatas = []
            
            for i, chunk in enumerate(chunks):
                chunk_id = f"{document_id}_chunk_{i}"
                ids.append(chunk_id)
                documents.append(chunk['text'])
                
                # Prepare metadata
                metadata = {
                    'document_id': document_id,
                    'document_path': document_path,
                    'chunk_index': i,
                    'page': chunk.get('page', 0),
                    'chunk_type': chunk.get('metadata', {}).get('section_type', 'content'),
                    'has_equations': chunk.get('metadata', {}).get('has_equations', False),
                    'has_tables': chunk.get('metadata', {}).get('has_tables', False),
                    'has_figures': chunk.get('metadata', {}).get('has_figures', False),
                    'created_at': datetime.now().isoformat(),
                    'text_length': len(chunk['text'])
                }
                
                # Add elements information
                if 'elements' in chunk:
                    metadata['elements_count'] = len(chunk['elements'])
                    metadata['elements'] = json.dumps(chunk['elements'])
                
                metadatas.append(metadata)
            
            # Add to collection
            self.collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas
            )
            
            logger.info(f"Added {len(chunks)} chunks for document {document_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to add document chunks: {e}")
            return False
    
    async def search_similar(self, query: str, n_results: int = 5,
                           filter_metadata: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """
        Search for similar chunks using semantic similarity.
        
        Args:
            query: Search query
            n_results: Number of results to return
            filter_metadata: Optional metadata filters
            
        Returns:
            List of similar chunks with scores
        """
        try:
            # Perform similarity search
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where=filter_metadata,
                include=['documents', 'metadatas', 'distances']
            )
            
            # Format results
            formatted_results = []
            
            if results['documents'] and len(results['documents']) > 0:
                for i in range(len(results['documents'][0])):
                    result = {
                        'text': results['documents'][0][i],
                        'metadata': results['metadatas'][0][i],
                        'similarity_score': 1 - results['distances'][0][i],  # Convert distance to similarity
                        'chunk_id': results['ids'][0][i] if 'ids' in results else None
                    }
                    formatted_results.append(result)
            
            logger.info(f"Found {len(formatted_results)} similar chunks for query: {query[:50]}...")
            return formatted_results
            
        except Exception as e:
            logger.error(f"Similarity search failed: {e}")
            return []
    
    async def search_by_document(self, document_id: str) -> List[Dict[str, Any]]:
        """
        Get all chunks for a specific document.
        
        Args:
            document_id: Document identifier
            
        Returns:
            List of chunks for the document
        """
        try:
            results = self.collection.get(
                where={"document_id": document_id},
                include=['documents', 'metadatas']
            )
            
            formatted_results = []
            if results['documents']:
                for i in range(len(results['documents'])):
                    result = {
                        'text': results['documents'][i],
                        'metadata': results['metadatas'][i],
                        'chunk_id': results['ids'][i]
                    }
                    formatted_results.append(result)
            
            # Sort by chunk index
            formatted_results.sort(key=lambda x: x['metadata'].get('chunk_index', 0))
            
            return formatted_results
            
        except Exception as e:
            logger.error(f"Document search failed: {e}")
            return []
    
    async def delete_document(self, document_id: str) -> bool:
        """
        Delete all chunks for a document.
        
        Args:
            document_id: Document identifier
            
        Returns:
            True if successful
        """
        try:
            # Get all chunk IDs for the document
            results = self.collection.get(
                where={"document_id": document_id},
                include=['ids']
            )
            
            if results['ids']:
                self.collection.delete(ids=results['ids'])
                logger.info(f"Deleted {len(results['ids'])} chunks for document {document_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"Document deletion failed: {e}")
            return False
    
    def get_collection_stats(self) -> Dict[str, Any]:
        """Get statistics about the collection."""
        try:
            count = self.collection.count()
            
            # Get sample of documents to analyze
            sample_results = self.collection.peek(limit=100)
            
            stats = {
                'total_chunks': count,
                'total_documents': len(set(
                    meta.get('document_id', '') 
                    for meta in sample_results.get('metadatas', [])
                )) if sample_results.get('metadatas') else 0,
                'persist_directory': str(self.persist_directory),
                'embedding_model': self.embedding_manager.model_name
            }
            
            # Analyze content types
            if sample_results.get('metadatas'):
                has_equations = sum(1 for meta in sample_results['metadatas'] 
                                  if meta.get('has_equations', False))
                has_tables = sum(1 for meta in sample_results['metadatas'] 
                               if meta.get('has_tables', False))
                has_figures = sum(1 for meta in sample_results['metadatas'] 
                                if meta.get('has_figures', False))
                
                stats.update({
                    'chunks_with_equations': has_equations,
                    'chunks_with_tables': has_tables,
                    'chunks_with_figures': has_figures
                })
            
            return stats
            
        except Exception as e:
            logger.error(f"Failed to get collection stats: {e}")
            return {'error': str(e)}
    
    async def hybrid_search(self, query: str, n_results: int = 5,
                          alpha: float = 0.7) -> List[Dict[str, Any]]:
        """
        Perform hybrid search combining semantic and keyword matching.
        
        Args:
            query: Search query
            n_results: Number of results to return
            alpha: Weight for semantic search (1-alpha for keyword search)
            
        Returns:
            List of ranked results
        """
        try:
            # Semantic search
            semantic_results = await self.search_similar(query, n_results * 2)
            
            # Simple keyword search (can be enhanced with BM25)
            keyword_results = []
            query_words = query.lower().split()
            
            # Get all documents for keyword matching
            all_results = self.collection.get(include=['documents', 'metadatas'])
            
            if all_results['documents']:
                for i, doc in enumerate(all_results['documents']):
                    doc_lower = doc.lower()
                    keyword_score = sum(1 for word in query_words if word in doc_lower)
                    
                    if keyword_score > 0:
                        keyword_results.append({
                            'text': doc,
                            'metadata': all_results['metadatas'][i],
                            'keyword_score': keyword_score / len(query_words),
                            'chunk_id': all_results['ids'][i]
                        })
            
            # Combine and rank results
            combined_results = {}
            
            # Add semantic results
            for result in semantic_results:
                chunk_id = result['chunk_id']
                combined_results[chunk_id] = result.copy()
                combined_results[chunk_id]['final_score'] = alpha * result['similarity_score']
            
            # Add keyword results
            for result in keyword_results:
                chunk_id = result['chunk_id']
                if chunk_id in combined_results:
                    combined_results[chunk_id]['final_score'] += (1 - alpha) * result['keyword_score']
                else:
                    combined_results[chunk_id] = result.copy()
                    combined_results[chunk_id]['final_score'] = (1 - alpha) * result['keyword_score']
                    combined_results[chunk_id]['similarity_score'] = 0
            
            # Sort by final score and return top results
            final_results = list(combined_results.values())
            final_results.sort(key=lambda x: x['final_score'], reverse=True)
            
            return final_results[:n_results]
            
        except Exception as e:
            logger.error(f"Hybrid search failed: {e}")
            return await self.search_similar(query, n_results)  # Fallback to semantic search
    
    def reset_collection(self):
        """Reset the collection (delete all data)."""
        try:
            self.client.reset()
            self._initialize_collection()
            logger.info("Collection reset successfully")
        except Exception as e:
            logger.error(f"Collection reset failed: {e}")
    
    def close(self):
        """Close the database connection."""
        # ChromaDB handles persistence automatically
        logger.info("ChromaDB connection closed")
