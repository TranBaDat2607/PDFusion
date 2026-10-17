"""The ONNX embedding function that replaced sentence-transformers.

Everything here runs against a fake session and tokenizer — the real weights are
a download of 118 MB or more, and the parts worth testing are the things this module
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
    pool,
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
    # The weights are a 118 MB+ download; ChromaDBManager builds this at
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


class _RecordingTokenizer:
    """Records the texts it was asked to encode; every text becomes [1, 2]."""

    def __init__(self):
        self.texts = []

    def encode_batch(self, texts):
        self.texts.extend(texts)
        return [_FakeEncoding([1, 2], [1, 1]) for _ in texts]


def _loaded(model_name):
    """An embedding function with a fake model already loaded."""
    ef = OnnxEmbeddingFunction(model_name)
    ef._tokenizer = _RecordingTokenizer()
    ef._session = _FakeSession(np.array([[[1.0, 0.0], [0.0, 1.0]]], dtype=np.float32))
    ef._input_names = ("input_ids", "attention_mask")
    return ef


def test_an_e5_model_reads_passages_and_queries_under_their_prefixes():
    """The e5 family was trained with `query: ` and `passage: ` in front of
    every input; without them its retrieval quality drops (model card)."""
    ef = _loaded("intfloat/multilingual-e5-small")

    ef(["Giá đường bình quân"])
    ef.embed_query(["giá đường"])

    assert ef._tokenizer.texts == ["passage: Giá đường bình quân", "query: giá đường"]


def test_minilm_reads_text_as_it_is():
    ef = _loaded("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

    ef(["chunk"])
    ef.embed_query(["question"])

    assert ef._tokenizer.texts == ["chunk", "question"]


def test_cls_pooling_takes_the_first_token():
    """bge-m3's dense vector is its [CLS] token, normalized (model card)."""
    tokens = np.array([[[3.0, 4.0], [100.0, 0.0]]], dtype=np.float32)
    mask = np.array([[1, 1]], dtype=np.int64)

    pooled = pool(tokens, mask, "cls")

    assert pooled == pytest.approx(np.array([[0.6, 0.8]]))


def test_mean_pooling_is_the_default_pooling():
    tokens = np.array([[[1.0, 0.0], [3.0, 0.0], [100.0, 100.0]]], dtype=np.float32)
    mask = np.array([[1, 1, 0]], dtype=np.int64)

    assert pool(tokens, mask, "mean") == pytest.approx(mean_pool(tokens, mask))


def test_an_unknown_model_is_refused_at_construction():
    """Its pooling and prefixes are unknown, so its vectors would be wrong in
    a way no error reports."""
    with pytest.raises(KeyError):
        OnnxEmbeddingFunction("someone/unknown-model")


def test_the_default_model_reads_under_the_prefixes_it_was_trained_with():
    """The default is int8 multilingual-e5-small (`index_spec.py`): without
    `query: ` / `passage: ` it is a quietly worse model."""
    ef = _loaded(DEFAULT_MODEL)

    ef(["chunk"])
    ef.embed_query(["question"])

    assert ef._tokenizer.texts == ["passage: chunk", "query: question"]
