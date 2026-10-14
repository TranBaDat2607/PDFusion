"""A cross-encoder that re-scores retrieval's best chunks against the question.

A benchmark experiment, not part of the app: plugged into
`EnhancedRAGChain(reranker=...)` by `run.py --options '{"rerank": true}'`. It
gained one question in 39 at hit@1 for +0.5 s a question and a 113 MB
download, so the app ships without it (see README.md).

The two first-stage searches each judge a chunk without seeing the question
and the chunk together: the embedding compares two vectors made apart, and
BM25 counts words. A cross-encoder reads the pair at once, which is what tells
a table that answers "what was Europe's growth?" from a paragraph that merely
mentions Europe and growth. It is too slow to run over a whole document, so it
re-scores the fused shortlist only (`rag_chain.EnhancedRAGChain.retrieve`).

`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`: trained on mMARCO, MS MARCO
machine-translated into 14 languages, Vietnamese among them; its AVX2 uint8
ONNX export is 113 MB and runs on the onnxruntime the embeddings already use.
Downloaded on first use to `~/.cache/huggingface`, like the embedding model.

Blocking and CPU-bound: call through `asyncio.to_thread`.
"""

from __future__ import annotations

import logging
import threading
from typing import List, Sequence

import numpy as np

logger = logging.getLogger(__name__)

RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
# The quantised export for x86 CPUs with AVX2, which is every x86 CPU the
# app's onnxruntime wheel supports. The fp32 export is 449 MB.
RERANKER_FILE = "onnx/model_quint8_avx2.onnx"
# The question and a chunk together. A chunk is at most ~150 tokens
# (`ingest.MAX_CHUNK_CHARS`), a question rarely 50.
_MAX_LENGTH = 256
_BATCH = 16


class OnnxCrossEncoder:
    """`scores(question, texts)` → one relevance score per text, higher better."""

    def __init__(self, model_name: str = RERANKER_MODEL, filename: str = RERANKER_FILE) -> None:
        self.model_name = model_name
        self.filename = filename
        self._session = None
        self._tokenizer = None
        self._input_names: tuple = ()
        self._lock = threading.Lock()

    def _load(self) -> None:
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            import onnxruntime
            from huggingface_hub import hf_hub_download
            from tokenizers import Tokenizer

            logger.info("Loading reranker %s", self.model_name)
            model_path = hf_hub_download(self.model_name, self.filename)
            tokenizer = Tokenizer.from_file(hf_hub_download(self.model_name, "tokenizer.json"))
            tokenizer.enable_truncation(max_length=_MAX_LENGTH)
            tokenizer.enable_padding()
            session = onnxruntime.InferenceSession(model_path, providers=["CPUExecutionProvider"])
            self._tokenizer = tokenizer
            self._input_names = tuple(i.name for i in session.get_inputs())
            self._session = session

    def scores(self, question: str, texts: Sequence[str]) -> List[float]:
        if not texts:
            return []
        self._load()
        out: List[float] = []
        for start in range(0, len(texts), _BATCH):
            batch = texts[start:start + _BATCH]
            encodings = self._tokenizer.encode_batch([(question, text) for text in batch])
            available = {
                "input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
                "token_type_ids": np.array([e.type_ids for e in encodings], dtype=np.int64),
            }
            feed = {name: available[name] for name in self._input_names if name in available}
            logits = self._session.run(None, feed)[0]
            out.extend(float(row[0]) for row in np.asarray(logits).reshape(len(batch), -1))
        return out
