"""The benchmark's interval statistics (`benchmarks/rag/stats.py`).

With ~100 questions, one question moves a rate by about a point, so every
rate the benchmark reports carries an interval, and a comparison between two
systems carries a paired one. Expected Wilson bounds are the textbook values
(Wilson, 1927; e.g. Newcombe, 1998, Table I).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks" / "rag"))

from stats import mcnemar_exact, paired_bootstrap, wilson  # noqa: E402


@pytest.mark.parametrize(
    ("k", "n", "low", "high"),
    [
        (8, 10, 0.4902, 0.9433),
        (0, 10, 0.0, 0.2775),
        (10, 10, 0.7225, 1.0),
    ],
)
def test_wilson_matches_the_textbook_bounds(k, n, low, high):
    lo, hi = wilson(k, n)
    assert lo == pytest.approx(low, abs=5e-4)
    assert hi == pytest.approx(high, abs=5e-4)


def test_wilson_of_nothing_is_the_whole_range():
    assert wilson(0, 0) == (0.0, 1.0)


def test_paired_bootstrap_of_identical_systems_is_zero():
    scores = [1.0, 0.0, 1.0, 1.0, 0.0]
    assert paired_bootstrap(scores, scores) == (0.0, 0.0, 0.0)


def test_paired_bootstrap_when_one_system_wins_every_question():
    delta, lo, hi = paired_bootstrap([0.0] * 6, [1.0] * 6)
    assert (delta, lo, hi) == (1.0, 1.0, 1.0)


def test_paired_bootstrap_interval_contains_the_observed_difference():
    before = [1, 0, 0, 1, 0, 1, 0, 0, 1, 0] * 3
    after = [1, 1, 0, 1, 1, 1, 0, 1, 1, 0] * 3
    delta, lo, hi = paired_bootstrap(before, after, iterations=2000, seed=3)
    assert delta == pytest.approx(0.3)
    assert lo <= delta <= hi
    assert lo > 0  # every pair moved the same way, or not at all


def test_paired_bootstrap_is_reproducible():
    a = [1, 0, 1, 0, 1, 1, 0]
    b = [0, 1, 1, 0, 1, 0, 1]
    assert paired_bootstrap(a, b, seed=11) == paired_bootstrap(a, b, seed=11)


def test_paired_bootstrap_refuses_unpaired_lists():
    with pytest.raises(ValueError):
        paired_bootstrap([1, 0], [1])


def test_mcnemar_exact_matches_a_hand_count():
    """8 questions only the new system gets right, 3 only the old: under
    the null each of the 11 is a fair coin, so p = 2 · Σ_{j≤3} C(11, j) / 2¹¹
    = 2 · 232 / 2048."""
    before = [0] * 8 + [1] * 3 + [1] * 20
    after = [1] * 8 + [0] * 3 + [1] * 20

    assert mcnemar_exact(before, after) == pytest.approx(464 / 2048)


def test_mcnemar_exact_with_no_disagreement_is_one():
    assert mcnemar_exact([1, 0, 1], [1, 0, 1]) == 1.0
