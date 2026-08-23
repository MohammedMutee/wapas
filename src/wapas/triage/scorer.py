"""How likely is this episode to recover if we work it?

Needed because the decision to act at all is a decision. Every other component
here assumes the answer is yes and asks *what* to do; this one asks whether the
episode is worth touching, which is the only question whose right answer is
sometimes "leave this person alone".

**Empirical rates, not gradient boosting.** The build plan called for LightGBM
with isotonic calibration, and that was the wrong tool once the feature set was
known. What is observable before the first action is a handful of low-cardinality
categoricals — cause, surface, amount band — over a few thousand resolved
episodes. A smoothed conditional rate on that data is *perfectly calibrated by
construction*, because it is the observed frequency rather than a score mapped
onto one; it is interpretable, deterministic, and adds no dependency. A boosted
tree would have been a heavier way to reproduce a lookup table, and its
calibration would then have needed its own machinery to fix.

**Trained on resolved history, applied to the present.** The same split
everything else uses: a separate population from a separate seed, worked
through the same engine, whose outcomes are now known. It never sees an
evaluation episode.

**Hierarchical backoff.** A cell with four episodes in it is not evidence.
Estimates fall back cause+surface+band → cause+surface → cause → global until
they have support, and the level that answered is reported, so a caller can see
how specific the evidence was.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..domain import RootCause, Surface
from ..money import Paise


def amount_band(amount: Paise) -> str:
    """The same bands the diagnosis prompt uses, for the same reason: exact
    rupees carry no signal a rate can use, and banding keeps cells populated."""
    rupees = int(amount) / 100
    if rupees < 500:
        return "<500"
    if rupees < 2_000:
        return "500-2k"
    if rupees < 10_000:
        return "2k-10k"
    if rupees < 50_000:
        return "10k-50k"
    return "50k+"


@dataclass(frozen=True, slots=True)
class Estimate:
    probability: float
    support: int
    level: str
    """Which backoff level supplied it. ``global`` means we know almost nothing."""

    @property
    def confident(self) -> bool:
        return self.level != "global" and self.support >= 60


@dataclass
class RecoverabilityScorer:
    """P(recovered | cause, surface, amount band), from worked history."""

    min_support: int = 30
    counts: dict[tuple, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))
    """key -> [recovered, total]."""

    @staticmethod
    def _keys(cause: RootCause, surface: Surface, band: str) -> list[tuple]:
        return [
            ("cause_surface_band", str(cause), str(surface), band),
            ("cause_surface", str(cause), str(surface)),
            ("cause", str(cause)),
            ("global",),
        ]

    def observe(self, *, cause: RootCause, surface: Surface, amount: Paise,
                recovered: bool) -> None:
        for key in self._keys(cause, surface, amount_band(amount)):
            cell = self.counts[key]
            cell[0] += int(recovered)
            cell[1] += 1

    @classmethod
    def from_results(cls, results, **kwargs) -> RecoverabilityScorer:
        """Learn from episodes already worked to a terminal state."""
        scorer = cls(**kwargs)
        for r in results:
            if r.true_cause is None:
                continue
            scorer.observe(cause=r.true_cause, surface=Surface(r.surface),
                           amount=r.amount_paise, recovered=r.recovered)
        return scorer

    def estimate(self, *, cause: RootCause, surface: Surface, amount: Paise) -> Estimate:
        for key in self._keys(cause, surface, amount_band(amount)):
            recovered, total = self.counts.get(key, [0, 0])
            if total >= self.min_support:
                return Estimate(recovered / total, total, key[0])
        recovered, total = self.counts.get(("global",), [0, 0])
        return Estimate(recovered / total if total else 0.5, total, "global")

    # ── the check that decides whether to believe it ─────────────────────────

    def reliability(self, results, bins: int = 5) -> list[tuple[float, float, int]]:
        """Predicted probability against observed frequency, in bins.

        A probability that is only a ranking is useless to an expected-value
        calculation: multiplying a miscalibrated 0.8 by an amount produces a
        confident wrong number. This is the check, and it is run against
        episodes the scorer never saw.
        """
        buckets: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        totals: dict[int, float] = defaultdict(float)
        for r in results:
            if r.true_cause is None:
                continue
            p = self.estimate(cause=r.true_cause, surface=Surface(r.surface),
                              amount=r.amount_paise).probability
            index = min(bins - 1, int(p * bins))
            buckets[index][0] += int(r.recovered)
            buckets[index][1] += 1
            totals[index] += p
        return [
            (totals[i] / buckets[i][1], buckets[i][0] / buckets[i][1], buckets[i][1])
            for i in sorted(buckets) if buckets[i][1]
        ]

    def calibration_error(self, results, bins: int = 5) -> float:
        """Expected calibration error: mean absolute gap between predicted and observed, weighted."""
        rows = self.reliability(results, bins)
        n = sum(count for _, _, count in rows)
        if not n:
            return 0.0
        return sum(abs(pred - obs) * count for pred, obs, count in rows) / n
