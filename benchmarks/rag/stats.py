"""Intervals for the benchmark's rates and comparisons. Stdlib-only.

The corpus is ~100 questions, so one question moves a rate by about a point
and a category of four questions by 25. A rate is reported with its Wilson
interval, which unlike the normal approximation stays inside [0, 1] and is
honest at 0/n and n/n. Two systems answer the same questions, so they are
compared by a paired bootstrap over questions: what varies between runs is
which questions were asked, and a question one system gets right is usually
one the other does too.
"""

from __future__ import annotations

import math
import random
from typing import Sequence, Tuple

Z_95 = 1.959964


def wilson(k: int, n: int, z: float = Z_95) -> Tuple[float, float]:
    """The Wilson score interval for k successes in n trials."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def paired_bootstrap(
    before: Sequence[float],
    after: Sequence[float],
    iterations: int = 5000,
    seed: int = 0,
    confidence: float = 0.95,
) -> Tuple[float, float, float]:
    """`after`'s mean minus `before`'s, with a percentile interval from
    resampling the questions both answered. Seeded: the same lists give the
    same interval on every run, so a report doesn't change when re-printed."""
    if len(before) != len(after):
        raise ValueError("a paired comparison needs one score per question from each system")
    n = len(before)
    if n == 0:
        return 0.0, 0.0, 0.0
    diffs = [float(b) - float(a) for a, b in zip(before, after)]
    delta = sum(diffs) / n
    rng = random.Random(seed)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations))
    tail = (1 - confidence) / 2
    low = means[int(tail * (iterations - 1))]
    high = means[int(round((1 - tail) * (iterations - 1)))]
    return round(delta, 6), round(low, 6), round(high, 6)


def mcnemar_exact(before: Sequence[float], after: Sequence[float]) -> float:
    """Two-sided exact McNemar p-value for paired pass/fail scores.

    Only the questions the two systems disagree on carry information; under
    "no difference" each is a fair coin. With this few yes/no outcomes the
    bootstrap above can be the less cautious of the two — all-one-way
    disagreements put its lower bound at exactly 0 — so both are reported.
    """
    if len(before) != len(after):
        raise ValueError("a paired comparison needs one score per question from each system")
    gained = sum(1 for a, b in zip(before, after) if b > a)
    lost = sum(1 for a, b in zip(before, after) if b < a)
    n = gained + lost
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, j) for j in range(min(gained, lost) + 1))
    return min(1.0, 2 * tail / 2 ** n)
