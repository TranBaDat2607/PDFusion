"""The ONNX embedding function that replaced sentence-transformers.

Everything here runs against a fake session and tokenizer — the real weights are
a ~470 MB download, and the parts worth testing are the two things this module
reimplements from sentence-transformers: the input feed and the mean pooling.
"""

import numpy as np
import pytest

from desktop_pdf_translator.rag.onnx_embeddings import (
    DEFAULT_MODEL,
    OnnxEmbeddingFunction,
    build_feed,
    embed_batch,
    mean_pool,
)


class _FakeEncoding:
    def __init__(self, ids, attention_mask):
        self.ids = ids
        self.attention_mask = attention_mask


class _FakeTokenizer:
    def __init__(self, encodings):
        self._encodings = encodings

    def encode_batch(self, texts):
        return self._encodings[: len(texts)]


class _FakeSession:
    """Returns a fixed token-embedding tensor and records the feed it was given."""

    def __init__(self, output):
        self.output = output
        self.last_feed = None

    def run(self, _outputs, feed):
        self.last_feed = feed
        return [self.output]


def test_padding_tokens_are_excluded_from_the_mean():
    # Two real tokens ([1,0] and [3,0]) plus one padding token that would drag
    # the mean sideways if the attention mask were ignored.
    tokens = np.array([[[1.0, 0.0], [3.0, 0.0], [100.0, 100.0]]], dtype=np.float32)
    mask = np.array([[1, 1, 0]], dtype=np.int64)

    pooled = mean_pool(tokens, mask)

    assert pooled == pytest.approx(np.array([[1.0, 0.0]]))


def test_pooled_vectors_are_unit_length():
    tokens = np.array([[[3.0, 4.0], [3.0, 4.0]]], dtype=np.float32)
    mask = np.array([[1, 1]], dtype=np.int64)

    pooled = mean_pool(tokens, mask)

    assert np.linalg.norm(pooled[0]) == pytest.approx(1.0)


def test_an_all_padding_row_does_not_divide_by_zero():
    tokens = np.zeros((1, 2, 2), dtype=np.float32)
    mask = np.array([[0, 0]], dtype=np.int64)

    pooled = mean_pool(tokens, mask)

    assert np.isfinite(pooled).all()


def test_the_feed_matches_what_the_export_declares():
    ids = np.array([[1, 2]], dtype=np.int64)
    mask = np.array([[1, 1]], dtype=np.int64)

    # The published export is a BertModel, so it takes token_type_ids…
    bert = build_feed(("input_ids", "attention_mask", "token_type_ids"), ids, mask)
    assert sorted(bert) == ["attention_mask", "input_ids", "token_type_ids"]
    assert (bert["token_type_ids"] == 0).all()

    # …but a re-export without them must not be handed an argument it rejects.
    lean = build_feed(("input_ids", "attention_mask"), ids, mask)
    assert sorted(lean) == ["attention_mask", "input_ids"]


def test_embed_batch_feeds_the_tokenizer_output_through_to_pooling():
    tokenizer = _FakeTokenizer([_FakeEncoding([5, 6, 0], [1, 1, 0])])
    session = _FakeSession(
        np.array([[[1.0, 0.0], [3.0, 0.0], [100.0, 100.0]]], dtype=np.float32)
    )

    result = embed_batch(session, tokenizer, ("input_ids", "attention_mask"), ["hello"])

    assert (session.last_feed["input_ids"] == np.array([[5, 6, 0]])).all()
    assert (session.last_feed["attention_mask"] == np.array([[1, 1, 0]])).all()
    assert result == [pytest.approx([1.0, 0.0])]


def test_construction_does_not_load_the_model():
    # The weights are a ~470 MB download; ChromaDBManager builds this at
    # startup, so it must stay free until something actually embeds.
    ef = OnnxEmbeddingFunction()

    assert ef._session is None
    assert ef._tokenizer is None


def test_config_round_trips_through_build_from_config():
    ef = OnnxEmbeddingFunction()

    assert ef.name() == "pdfusion-onnx-minilm"
    assert ef.default_space() == "cosine"
    assert ef.get_config() == {"model_name": DEFAULT_MODEL}

    rebuilt = OnnxEmbeddingFunction.build_from_config(ef.get_config())
    assert rebuilt.model_name == ef.model_name
