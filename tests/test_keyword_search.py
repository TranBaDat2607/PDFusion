"""`rag/keyword_search.py`: BM25 over one index's chunks (#31).

Pure Python, no stores. What the keyword side of retrieval has to get right is
which chunk a word points at, and that its scores can be blended with
similarities in [0, 1].
"""

from __future__ import annotations

from desktop_pdf_translator.rag.keyword_search import Bm25Index, tokenize


def test_words_are_folded_the_way_the_viewers_find_folds_them():
    assert tokenize("Việt Nam ĐANG phát triển") == ["viet", "nam", "dang", "phat", "trien"]
    assert tokenize("Thermal CONDUCTIVITY, 42%") == ["thermal", "conductivity", "42"]


def test_a_rare_word_outranks_a_common_one():
    """Every chunk says "measure" and one says "graphene". The substring count
    this replaced gave both words the same weight."""
    index = Bm25Index([
        "We measure the samples.",
        "We measure graphene samples.",
        "We measure the results again.",
    ])

    scores = index.scores("measure graphene")

    assert scores[1] == 1.0
    assert all(0 < score < 1 for position, score in enumerate(scores) if position != 1)


def test_a_word_inside_another_word_is_not_a_match():
    """`"rate" in "accurate"` used to count as a hit."""
    index = Bm25Index(["The results are accurate.", "The error rate fell."])

    assert index.scores("rate") == [0.0, 1.0]


def test_a_question_typed_without_vietnamese_marks_finds_text_with_them():
    index = Bm25Index(["Kết quả thí nghiệm", "Phương pháp đo"])

    assert index.scores("phuong phap do") == [0.0, 1.0]


def test_scores_are_all_zero_when_nothing_matches():
    index = Bm25Index(["alpha beta", "gamma"])

    assert index.scores("delta") == [0.0, 0.0]
    assert index.scores("") == [0.0, 0.0]
    assert index.scores(" ,;: ") == [0.0, 0.0]


def test_an_empty_index_scores_nothing():
    index = Bm25Index([])

    assert len(index) == 0
    assert index.scores("anything") == []


def test_a_word_repeated_in_the_question_counts_once():
    index = Bm25Index(["graphene sheet", "copper sheet"])

    assert index.scores("graphene graphene graphene sheet") == index.scores("graphene sheet")


def test_of_two_chunks_with_the_same_hit_the_shorter_ranks_higher():
    index = Bm25Index(["graphene " + "filler " * 40, "graphene result"])

    longer, shorter = index.scores("graphene")

    assert shorter == 1.0
    assert 0 < longer < shorter
