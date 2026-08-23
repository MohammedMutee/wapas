"""Statistics for the evaluation, and the calibration that keeps them honest.

Four choices worth defending.

**Bootstrap over episodes, not rupees.** Recovery amounts are heavy-tailed
(lognormal, long right tail), so resampling rupees would understate uncertainty
badly — a handful of large invoices carry most of the total.

**Stratified permutation as the decision rule.** The percentile bootstrap is
approximate: with heavy tails and unequal arm sizes it under-covers, which is
exactly how an earlier run of this report produced a *false positive on an A/A
comparison*. The permutation test is exact under the sharp null of
exchangeability — no distributional assumption, no large-sample argument — so
it is what decides whether a difference is claimed. The bootstrap interval is
still reported, because a p-value alone tells a reader nothing about size.

**Permute within strata.** Episodes are assigned to arms stratified by amount
decile (see ``wapas.engine.stratified_assignment``). Exchangeability therefore
holds *within* a decile, not across them, and the test must respect the design
that produced the data. Permuting across strata would be testing a null the
experiment never ran.

**Calibrate, do not assume.** ``eval.calibrate`` runs the whole comparison on
many seeds where the true difference is known to be zero and reports the
measured false-positive rate. A nominal 95% interval that fires 20% of the time
is not a 95% interval, and the only way to know is to check.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RESAMPLES = 10_000
CONFIDENCE = 0.95


@dataclass(frozen=True, slots=True)
class Interval:
    point: float
    low: float
    high: float

    def scaled(self, factor: float) -> Interval:
        return Interval(self.point * factor, self.low * factor, self.high * factor)

    @property
    def excludes_zero(self) -> bool:
        return self.low > 0 or self.high < 0

    def __str__(self) -> str:
        return f"{self.point:,.0f} [{self.low:,.0f}, {self.high:,.0f}]"


@dataclass(frozen=True, slots=True)
class Comparison:
    """One arm against another, per episode, with both verdicts.

    ``interval`` is a bootstrap CI on the difference in per-episode means.
    ``p_value`` is the two-sided stratified permutation p-value. ``null_band``
    is the middle 95% of the permutation null — the noise floor: a difference
    inside it is indistinguishable from relabelling the same episodes.
    """

    label: str
    n_treatment: int
    n_other: int
    interval: Interval
    p_value: float
    null_band: Interval

    @property
    def significant(self) -> bool:
        """The claim rule. Permutation decides; the CI is descriptive."""
        return self.p_value < 0.05

    def verdict(self) -> str:
        if not self.significant:
            return "no — indistinguishable from noise"
        return "yes" if self.interval.point > 0 else "worse"


def _as_array(values: list[float]) -> np.ndarray:
    return np.asarray(values, dtype=np.float64)


def bootstrap_difference(
    treatment: list[float],
    other: list[float],
    *,
    seed: int,
    resamples: int = RESAMPLES,
    confidence: float = CONFIDENCE,
) -> Interval:
    """Percentile-bootstrap CI for the difference in per-episode means.

    Reported per episode. Callers scale it — to 1,000 episodes for a rate, or
    to the treatment arm's size for a total.
    """
    if not treatment or not other:
        return Interval(0.0, 0.0, 0.0)

    t, c = _as_array(treatment), _as_array(other)
    rng = np.random.default_rng(seed)
    point = float(t.mean() - c.mean())

    t_draws = rng.integers(0, t.size, size=(resamples, t.size))
    c_draws = rng.integers(0, c.size, size=(resamples, c.size))
    diffs = t[t_draws].mean(axis=1) - c[c_draws].mean(axis=1)

    alpha = (1 - confidence) / 2
    low, high = np.quantile(diffs, [alpha, 1 - alpha])
    return Interval(point, float(low), float(high))


def permutation_p(
    treatment: list[float],
    other: list[float],
    *,
    seed: int,
    strata: tuple[list[int], list[int]] | None = None,
    resamples: int = RESAMPLES,
) -> tuple[float, Interval]:
    """Two-sided permutation test on the difference in per-episode means.

    Returns ``(p_value, null_band)`` where the band is the middle 95% of the
    null distribution. If ``strata`` is given — parallel stratum labels for the
    two arms — labels are shuffled *within* each stratum, matching how the
    experiment actually allocated episodes.

    The p-value uses the ``(hits + 1) / (resamples + 1)`` convention, so it can
    never be reported as exactly zero. Claiming p = 0 from 10,000 resamples is
    claiming more precision than the procedure has.
    """
    if not treatment or not other:
        return 1.0, Interval(0.0, 0.0, 0.0)

    t, c = _as_array(treatment), _as_array(other)
    observed = float(t.mean() - c.mean())
    rng = np.random.default_rng(seed)

    if strata is None:
        groups = [(t, c)]
    else:
        t_lab = np.asarray(strata[0])
        c_lab = np.asarray(strata[1])
        groups = []
        for key in np.unique(np.concatenate([t_lab, c_lab])):
            groups.append((t[t_lab == key], c[c_lab == key]))

    # Sum of the permuted treatment values, accumulated stratum by stratum.
    t_sums = np.zeros(resamples)
    c_sums = np.zeros(resamples)
    for t_g, c_g in groups:
        pooled = np.concatenate([t_g, c_g])
        n_t = t_g.size
        if pooled.size == 0:
            continue
        if n_t == 0 or n_t == pooled.size:
            # A stratum present in only one arm carries no information about
            # the difference, but its values still belong in that arm's mean.
            t_sums += t_g.sum()
            c_sums += c_g.sum()
            continue
        order = rng.random((resamples, pooled.size)).argsort(axis=1)
        drawn = pooled[order]
        t_sums += drawn[:, :n_t].sum(axis=1)
        c_sums += drawn[:, n_t:].sum(axis=1)

    null = t_sums / t.size - c_sums / c.size
    hits = int(np.count_nonzero(np.abs(null) >= abs(observed)))
    p_value = (hits + 1) / (resamples + 1)
    low, high = np.quantile(null, [0.025, 0.975])
    return p_value, Interval(observed, float(low), float(high))


def compare(
    label: str,
    treatment: list[float],
    other: list[float],
    *,
    seed: int,
    strata: tuple[list[int], list[int]] | None = None,
    resamples: int = RESAMPLES,
) -> Comparison:
    """Bootstrap interval plus permutation verdict for one pair of arms."""
    interval = bootstrap_difference(treatment, other, seed=seed, resamples=resamples)
    p_value, null_band = permutation_p(
        treatment, other, seed=seed + 1, strata=strata, resamples=resamples
    )
    return Comparison(
        label=label,
        n_treatment=len(treatment),
        n_other=len(other),
        interval=interval,
        p_value=p_value,
        null_band=null_band,
    )


def rate_difference(
    treatment: list[bool], other: list[bool], *, seed: int, resamples: int = RESAMPLES
) -> Interval:
    """Difference in proportions, in percentage points, with a bootstrap CI."""
    return bootstrap_difference(
        [100.0 if x else 0.0 for x in treatment],
        [100.0 if x else 0.0 for x in other],
        seed=seed,
        resamples=resamples,
    )
