"""`rag/retrieval.py`: fusing rankings and grouping hits into page evidence.

Pure functions over dicts. What matters is that a page is ranked by its best
hit, that context never leaks across a page boundary, and that the choice of
pages to send as images follows rank order.
"""

from __future__ import annotations

import pytest

from desktop_pdf_translator.rag.retrieval import (
    PageEvidence,
    chunk_map,
    group_by_page,
    reciprocal_rank_fusion,
    select_image_pages,
)


def make_chunk(cid, page, idx, page_kind="text", kind="text"):
    return {
        "chunk_id": cid,
        "text": f"text of {cid}",
        "metadata": {"page": page, "chunk_index": idx, "page_kind": page_kind, "kind": kind},
    }


def evidence(page, page_kind="text", kinds=("text",)):
    chunks = [make_chunk(f"p{page}-{i}", page, i, page_kind, kind) for i, kind in enumerate(kinds)]
    return PageEvidence(page=page, score=1.0, chunks=chunks, matched_ids=set(), page_kind=page_kind)


def doc():
    # page 0: c0 c1 c2   page 1: c3 c4 c5   page 2: c6
    return [
        make_chunk("c0", 0, 0),
        make_chunk("c1", 0, 1),
        make_chunk("c2", 0, 2),
        make_chunk("c3", 1, 3),
        make_chunk("c4", 1, 4),
        make_chunk("c5", 1, 5),
        make_chunk("c6", 2, 6),
    ]


# --- reciprocal_rank_fusion -------------------------------------------------


def test_an_id_in_both_rankings_sums_its_two_reciprocal_ranks():
    fused = dict(reciprocal_rank_fusion([["a", "b"], ["b", "c"]], k=60))
    # a: 1/(60+1); b: 1/(60+2) + 1/(60+1); c: 1/(60+2)
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["c"] == pytest.approx(1 / 62)


def test_fused_results_are_sorted_by_score_descending():
    # b = 1/62 + 1/61 > a = 1/61 > c = 1/62
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]])
    assert [cid for cid, _ in fused] == ["b", "a", "c"]


def test_every_id_appears_exactly_once():
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"], ["c", "a"]])
    assert sorted(cid for cid, _ in fused) == ["a", "b", "c"]
    assert len(fused) == 3


def test_ties_keep_first_appearance_order():
    # x and y each get 1/61 from their own ranking: a tie. x was scanned first.
    fused = reciprocal_rank_fusion([["x"], ["y"]])
    assert [cid for cid, _ in fused] == ["x", "y"]
    assert fused[0][1] == pytest.approx(1 / 61)
    assert fused[1][1] == pytest.approx(1 / 61)


def test_a_duplicate_inside_one_ranking_counts_once_at_its_best_position():
    fused = dict(reciprocal_rank_fusion([["a", "b", "a"]], k=60))
    assert fused["a"] == pytest.approx(1 / 61)  # not 1/61 + 1/63
    assert fused["b"] == pytest.approx(1 / 62)


def test_empty_input_gives_an_empty_result():
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_a_custom_k_changes_the_scores():
    fused = dict(reciprocal_rank_fusion([["a", "b"]], k=1))
    assert fused["a"] == pytest.approx(1 / 2)  # 1/(1+1)
    assert fused["b"] == pytest.approx(1 / 3)  # 1/(1+2)


# --- group_by_page ----------------------------------------------------------


def test_ids_that_are_not_chunks_of_the_document_are_ignored():
    result = group_by_page([("ghost", 0.9), ("c1", 0.5)], doc(), max_pages=5, neighbours=0)
    assert [e.page for e in result] == [0]
    assert result[0].matched_ids == {"c1"}


def test_pages_are_ranked_by_their_best_hit():
    fused = [("c4", 0.9), ("c1", 0.5), ("c6", 0.7)]
    result = group_by_page(fused, doc(), max_pages=5, neighbours=0)
    assert [e.page for e in result] == [1, 2, 0]
    assert [e.score for e in result] == [0.9, 0.7, 0.5]


def test_a_page_with_one_strong_hit_beats_a_page_with_many_weak_ones():
    fused = [("c3", 0.8), ("c0", 0.3), ("c1", 0.3), ("c2", 0.3)]
    result = group_by_page(fused, doc(), max_pages=5, neighbours=0)
    assert result[0].page == 1


def test_equal_best_scores_are_broken_by_the_larger_sum_of_hits():
    # both pages peak at 0.5; page 1 sums to 0.5 + 0.25 = 0.75, page 0 only 0.5
    fused = [("c0", 0.5), ("c3", 0.5), ("c4", 0.25)]
    result = group_by_page(fused, doc(), max_pages=5, neighbours=0)
    assert [e.page for e in result] == [1, 0]


def test_equal_best_and_sum_fall_back_to_the_lower_page_number():
    fused = [("c6", 0.5), ("c3", 0.5), ("c0", 0.5)]
    result = group_by_page(fused, doc(), max_pages=5, neighbours=0)
    assert [e.page for e in result] == [0, 1, 2]


def test_only_the_top_max_pages_are_returned():
    fused = [("c0", 0.9), ("c3", 0.8), ("c6", 0.7)]
    result = group_by_page(fused, doc(), max_pages=2, neighbours=0)
    assert [e.page for e in result] == [0, 1]


def test_neighbours_come_from_both_sides_of_a_match():
    result = group_by_page([("c4", 0.9)], doc(), max_pages=1, neighbours=1)
    assert [c["chunk_id"] for c in result[0].chunks] == ["c3", "c4", "c5"]
    assert result[0].matched_ids == {"c4"}


def test_a_neighbour_on_another_page_is_never_included():
    # c2 is the last chunk of page 0; c3 (next in the document) is on page 1.
    result = group_by_page([("c2", 0.9)], doc(), max_pages=1, neighbours=1)
    assert [c["chunk_id"] for c in result[0].chunks] == ["c1", "c2"]
    # and the first chunk of page 1 must not pull in the end of page 0
    result = group_by_page([("c3", 0.9)], doc(), max_pages=1, neighbours=1)
    assert [c["chunk_id"] for c in result[0].chunks] == ["c3", "c4"]


def test_zero_neighbours_gives_only_the_matched_chunks():
    result = group_by_page([("c1", 0.9)], doc(), max_pages=1, neighbours=0)
    assert [c["chunk_id"] for c in result[0].chunks] == ["c1"]


def test_two_adjacent_matches_do_not_duplicate_a_chunk():
    result = group_by_page([("c3", 0.9), ("c4", 0.8)], doc(), max_pages=1, neighbours=1)
    assert [c["chunk_id"] for c in result[0].chunks] == ["c3", "c4", "c5"]


def test_chunks_are_in_reading_order_whatever_order_they_were_given():
    shuffled = list(reversed(doc()))
    result = group_by_page([("c5", 0.9), ("c3", 0.8)], shuffled, max_pages=1, neighbours=1)
    assert [c["chunk_id"] for c in result[0].chunks] == ["c3", "c4", "c5"]


def test_page_kind_is_copied_from_the_pages_chunks():
    chunks = [make_chunk("s0", 0, 0, page_kind="scanned", kind="ocr"), make_chunk("t0", 1, 1)]
    result = group_by_page([("s0", 0.9), ("t0", 0.5)], chunks, max_pages=2, neighbours=0)
    assert [(e.page, e.page_kind) for e in result] == [(0, "scanned"), (1, "text")]


def test_group_by_page_does_not_ask_for_images():
    result = group_by_page([("c0", 0.9)], doc(), max_pages=1)
    assert len(result) == 1
    assert result[0].wants_image is False


# --- select_image_pages -----------------------------------------------------


def test_scanned_visual_and_table_pages_are_picked_in_rank_order():
    ev = [
        evidence(0, "text"),
        evidence(1, "scanned"),
        evidence(2, "text", kinds=("text", "table")),
        evidence(3, "visual"),
    ]
    picked = select_image_pages(ev, max_images=5)
    assert [e.page for e in picked] == [1, 2, 3]
    assert all(e.wants_image for e in picked)


def test_text_pages_stay_unmarked():
    ev = [evidence(0, "text"), evidence(1, "scanned")]
    select_image_pages(ev, max_images=5)
    assert ev[0].wants_image is False
    assert ev[1].wants_image is True


def test_the_number_of_images_is_capped_at_the_first_in_rank_order():
    ev = [evidence(5, "scanned"), evidence(2, "visual"), evidence(9, "scanned")]
    picked = select_image_pages(ev, max_images=2)
    assert [e.page for e in picked] == [5, 2]
    assert ev[2].wants_image is False


def test_zero_max_images_picks_nothing():
    ev = [evidence(0, "scanned")]
    assert select_image_pages(ev, max_images=0) == []
    assert ev[0].wants_image is False


# --- chunk_map --------------------------------------------------------------


def test_chunk_map_indexes_chunks_by_id():
    chunks = doc()
    mapped = chunk_map(chunks)
    assert set(mapped) == {f"c{i}" for i in range(7)}
    assert mapped["c4"] == chunks[4]
