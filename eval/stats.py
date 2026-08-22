"""Statistics for the evaluation.

Two choices worth defending:

**Bootstrap over episodes, not rupees.** Recovery amounts are heavy-tailed
(lognormal with a long right tail), so resampling rupees would understate the
uncertainty badly — a handful of large invoices dominate the total.

**Rate-scaled counterfactual.** The control arm is a different size from the
treatment arm, so its total cannot be subtracted directly. We compare
*per-episode recovery rates* and scale to the treatment arm's episode count.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import mean


@dataclass(frozen=True, slots=True)
class Interval:
    point: float
    low: float
    high: float

    def __str__(self) -> str:
        return f"{self.point:,.0f} [{self.low:,.0f}, {self.high:,.0f}]"


def bootstrap_difference(
    treatment: list[float], control: list[float], *, seed: int,
    resamples: int = 10_000, confidence: float = 0.95,
) -> Interval:
    """Bootstrap CI for the difference in per-episode means, scaled to |treatment|.

    Returns the *incremental* quantity: what the treatment arm recovered beyond
    what an untreated arm of the same size would have.
    """
    if not treatment or not control:
        return Interval(0.0, 0.0, 0.0)

    rng = random.Random(seed)
    n_t, n_c, scale = len(treatment), len(control), len(treatment)
    point = (mean(treatment) - mean(control)) * scale

    diffs = []
    for _ in range(resamples):
        t = mean(treatment[rng.randrange(n_t)] for _ in range(n_t))
        c = mean(control[rng.randrange(n_c)] for _ in range(n_c))
        diffs.append((t - c) * scale)
    diffs.sort()
    alpha = (1 - confidence) / 2
    return Interval(point, diffs[int(alpha * resamples)], diffs[int((1 - alpha) * resamples) - 1])


def rate_difference(
    treatment: list[bool], control: list[bool], *, seed: int, resamples: int = 10_000
) -> Interval:
    """Bootstrap CI for a difference in proportions, in percentage points."""
    return bootstrap_difference(
        [100.0 if x else 0.0 for x in treatment],
        [100.0 if x else 0.0 for x in control],
        seed=seed, resamples=resamples,
    ).__class__(
        *(v / max(1, len(treatment)) for v in (
            bootstrap_difference([100.0 if x else 0.0 for x in treatment],
                                 [100.0 if x else 0.0 for x in control],
                                 seed=seed, resamples=resamples).point,
            bootstrap_difference([100.0 if x else 0.0 for x in treatment],
                                 [100.0 if x else 0.0 for x in control],
                                 seed=seed, resamples=resamples).low,
            bootstrap_difference([100.0 if x else 0.0 for x in treatment],
                                 [100.0 if x else 0.0 for x in control],
                                 seed=seed, resamples=resamples).high,
        ))
    )
