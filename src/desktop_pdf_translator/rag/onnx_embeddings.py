"""ONNX embedding function for ChromaDB — no torch.

`sentence-transformers` pulled torch (466 MB) and transformers (119 MB) into the
installer for one `model.encode()` call. The models chat can use are published
with ONNX exports, and onnxruntime and tokenizers are already in the tree for
BabelDOC and ChromaDB. So this reimplements what sentence-transformers was
doing for us: put the prefix the model was trained with in front of each text,
tokenize, then pool the token embeddings — each model's way is its
`index_spec.EmbeddingSpec`.

The weights download on first Chat use into `~/.cache/huggingface`: 118 MB for
the int8 multilingual-e5-small the app uses (470 MB for the MiniLM before it).
Pre-bundling them is still open (issue #21).
"""

import logging
import threading
from typing import Any, Dict, List

import numpy as np
from chromadb import EmbeddingFunction, Embeddings

from .index_spec import EMBEDDING_MODEL, EMBEDDING_SPECS

logger = logging.getLogger(__name__)

# Recorded on every chat index (`rag/index_spec.py`), so changing it re-indexes
# documents rather than mixing two models' vectors in one index.
DEFAULT_MODEL = EMBEDDING_MODEL

# The default model's input limit: what chunk sizes are measured against
# (`ingest.MAX_CHUNK_CHARS`).
_MAX_SEQ_LENGTH = EMBEDDING_SPECS[DEFAULT_MODEL].max_tokens


class OnnxEmbeddingFunction(EmbeddingFunction):
    """ChromaDB embedding function backed by onnxruntime."""

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self.spec = EMBEDDING_SPECS[model_name]
        self._session = None
        self._tokenizer = None
        self._input_names: tuple = ()
        self._lock = threading.Lock()

    @staticmethod
    def name() -> str:
        # Kept from when the only model was MiniLM: ChromaDB records it on
        # every collection and refuses one opened under another name. Which
        # model a collection holds is `get_config()`'s, and the records'.
        return "pdfusion-onnx-minilm"

    def default_space(self) -> str:
        return "cosine"

    def get_config(self) -> Dict[str, Any]:
        return {"model_name": self.model_name}

    @staticmethod
    def build_from_config(config: Dict[str, Any]) -> "OnnxEmbeddingFunction":
        return OnnxEmbeddingFunction(model_name=config.get("model_name", DEFAULT_MODEL))

    def _load(self) -> None:
        """Fetch and open the model. Double-checked locking, so the download
        happens once on the first embed and not at construction."""
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return

            import onnxruntime
            from huggingface_hub import hf_hub_download
            from tokenizers import Tokenizer

            logger.info("Loading ONNX embedding model: %s", self.model_name)
            model_path = hf_hub_download(self.model_name, self.spec.onnx_file)
            tokenizer_path = hf_hub_download(self.model_name, "tokenizer.json")

            tokenizer = Tokenizer.from_file(tokenizer_path)
            tokenizer.enable_truncation(max_length=self.spec.max_tokens)
            tokenizer.enable_padding()

            session = onnxruntime.InferenceSession(
                model_path, providers=["CPUExecutionProvider"]
            )
            self._tokenizer = tokenizer
            self._input_names = tuple(i.name for i in session.get_inputs())
            self._session = session
            logger.info("ONNX embedding model ready (inputs: %s)", ", ".join(self._input_names))

    def __call__(self, input: List[str]) -> Embeddings:
        """Embed passages: what an index stores."""
        return self._embed([self.spec.passage_prefix + text for text in input])

    def embed_query(self, input: List[str]) -> Embeddings:
        """Embed questions. ChromaDB calls this for `query_texts`; a model
        trained with asymmetric prefixes reads a question differently."""
        return self._embed([self.spec.query_prefix + text for text in input])

    def _embed(self, texts: List[str]) -> Embeddings:
        self._load()
        return embed_batch(self._session, self._tokenizer, self._input_names, texts, self.spec.pooling)


def build_feed(
    input_names, ids: np.ndarray, mask: np.ndarray
) -> Dict[str, np.ndarray]:
    """Map tokenizer output onto whatever inputs this export actually declares.

    The published export is a BertModel with `type_vocab_size: 2`, so it takes
    `token_type_ids` — but reading the names off the session instead of
    hardcoding them means a re-export with a different signature still works.
    """
    available = {
        "input_ids": ids,
        "attention_mask": mask,
        "token_type_ids": np.zeros_like(ids),
    }
    return {name: available[name] for name in input_names if name in available}


def mean_pool(token_embeddings: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Mean-pool over non-padding tokens, then L2-normalize.

    Matches the model's `1_Pooling` config. Normalizing is not part of that
    config, but the collection's space is cosine, so it changes no ranking and
    keeps the vectors on a stable scale.
    """
    weights = mask[:, :, None].astype(np.float32)
    summed = (token_embeddings * weights).sum(axis=1)
    counts = np.clip(weights.sum(axis=1), 1e-9, None)
    pooled = summed / counts
    norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
    return pooled / norms


def pool(token_embeddings: np.ndarray, mask: np.ndarray, pooling: str) -> np.ndarray:
    """One unit vector per input, pooled the way the model was trained."""
    if pooling == "cls":
        first = token_embeddings[:, 0, :]
        norms = np.clip(np.linalg.norm(first, axis=1, keepdims=True), 1e-12, None)
        return first / norms
    return mean_pool(token_embeddings, mask)


def embed_batch(session, tokenizer, input_names, texts: List[str], pooling: str = "mean") -> Embeddings:
    encodings = tokenizer.encode_batch(texts)
    ids = np.array([e.ids for e in encodings], dtype=np.int64)
    mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
    token_embeddings = session.run(None, build_feed(input_names, ids, mask))[0]
    return pool(token_embeddings, mask, pooling).tolist()
