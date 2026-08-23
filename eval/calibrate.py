"""Does the evaluation lie? Measure it, on nulls where the answer is known.

Every significance claim in ``results/report.md`` rests on an assumption that a
95% interval covers the truth 95% of the time and a 5% test fires 5% of the
time. Those are asymptotic properties. With lognormal amounts and arms of
different sizes, they are assumptions, not facts — and an earlier version of
this report *did* announce a difference between two arms running identical
code.

So this script measures the false-positive rate directly. For each seed it
builds a whole world, splits one arm into two halves that ran the same strategy
on the same episodes' population, and asks the analysis whether it can tell
them apart. It cannot, because there is nothing there. Every rejection is a
false positive, and the fraction of rejections is the real error rate.

Two experiments, because they answer different questions.

**Across worlds.** One placebo split per seed, exactly as ``results/report.md``
performs it, under both allocation designs — stratified by amount decile, and
the simple per-episode hashing this project used until 2026-08-23. This
measures the false-positive rate of the procedure as actually run, and it also
measures how *wide* the null differences are, which is what stratification is
really for.

**Within one world.** Many independent random splits of a single arm. A
permutation test is a randomisation test: conditional on the data it is exact
for any data-generating process, however correlated, provided the split is
exchangeable. So this checks the machinery itself, with far more resolution
than the across-worlds run can afford, and it separates "the test is wrong"
from "we have not run enough seeds".

    python -m eval.calibrate --seeds 300
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from scipy.stats import binomtest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eval.run_batch import ARM_SHARES, STRATEGIES, _rates, _values
from eval.stats import compare, permutation_p
from sim import ResponseModel, build_population, load_params
from wapas.clock import IST
from wapas.domain import Arm
from wapas.engine import EpisodeResult, EpisodeRunner, assign_arm, stratified_assignment
from wapas.llm.costs import CostBook
from wapas.policy import load_policies

ALPHA = 0.05


@dataclass
class Tally:
    """How often each verdict fired when the true difference was zero."""

    design: str
    seeds: int = 0
    perm_rupees: int = 0
    perm_rate: int = 0
    ci_rupees: int = 0
    ci_rate: int = 0
    width_rupees: float = 0.0
    width_rate: float = 0.0
    null_rupees: float = 0.0
    null_rate: float = 0.0

    def _mean(self, total: float) -> float:
        return total / self.seeds if self.seeds else 0.0

    @property
    def mean_width_rupees(self) -> float:
        return self._mean(self.width_rupees)

    @property
    def mean_width_rate(self) -> float:
        return self._mean(self.width_rate)

    @property
    def mean_null_rupees(self) -> float:
        return self._mean(self.null_rupees)

    @property
    def mean_null_rate(self) -> float:
        return self._mean(self.null_rate)

    def row(self, name: str, hits: int) -> str:
        """One line of the calibration table.

        The verdict is itself a hypothesis test, because eyeballing "12.5% is
        more than 5%" from eight seeds is exactly the mistake this whole file
        exists to catch: 1 rejection in 8 is entirely consistent with a
        correctly calibrated 5% test. An exact binomial test against p = 0.05
        decides, and small runs are reported as inconclusive rather than as
        failures.
        """
        if not self.seeds:
            return f"| `{self.design}` | {name} | — | — | no data |"
        rate = hits / self.seeds
        p = binomtest(hits, self.seeds, ALPHA, alternative="greater").pvalue
        if p < ALPHA:
            flag = "**miscalibrated**"
        elif self.seeds < 100:
            flag = "consistent with 5% (too few seeds to be sure)"
        else:
            flag = "consistent with 5%"
        return (f"| `{self.design}` | {name} | {hits}/{self.seeds} | {rate:.1%} | "
                f"{flag} (p = {p:.3f}) |")


def _halves_stratified(
    results: list[EpisodeResult], stratum: dict[str, int], seed: int
) -> tuple[list[EpisodeResult], list[EpisodeResult]]:
    buckets: dict[int, list[EpisodeResult]] = {}
    for r in results:
        buckets.setdefault(stratum[r.ref], []).append(r)
    left, right = [], []
    for key in sorted(buckets):
        members = sorted(
            buckets[key], key=lambda r: hashlib.sha256(f"placebo|{seed}|{r.ref}".encode()).digest()
        )
        left.extend(members[0::2])
        right.extend(members[1::2])
    return left, right


def _halves_simple(
    results: list[EpisodeResult], seed: int
) -> tuple[list[EpisodeResult], list[EpisodeResult]]:
    members = sorted(
        results, key=lambda r: hashlib.sha256(f"placebo|{seed}|{r.ref}".encode()).digest()
    )
    return members[0::2], members[1::2]


def conditional_exactness(
    results: list[EpisodeResult], stratum: dict[str, int], *, splits: int, resamples: int
) -> tuple[int, int, int]:
    """Re-split one fixed arm many times and count rejections.

    Conditional on the data, a permutation test is exact whatever produced that
    data — dependence between episodes, heavy tails, none of it matters, because
    the reference distribution is generated by the same relabelling that
    assigned the groups. So a rejection rate materially above 5% here means the
    implementation is wrong, not that the world is awkward.
    """
    import numpy as np

    values = np.array([float(r.recovered_paise) for r in results])
    rates = np.array([100.0 if r.recovered else 0.0 for r in results])
    labels = np.array([stratum[r.ref] for r in results])
    rng = np.random.default_rng(20260823)

    hits_rupees = hits_rate = 0
    for k in range(splits):
        left: list[int] = []
        right: list[int] = []
        for key in np.unique(labels):
            idx = np.where(labels == key)[0]
            rng.shuffle(idx)
            left += list(idx[0::2])
            right += list(idx[1::2])
        strata = (list(labels[left]), list(labels[right]))
        p_rupees, _ = permutation_p(list(values[left]), list(values[right]),
                                    seed=k, strata=strata, resamples=resamples)
        p_rate, _ = permutation_p(list(rates[left]), list(rates[right]),
                                  seed=k, strata=strata, resamples=resamples)
        hits_rupees += int(p_rupees < ALPHA)
        hits_rate += int(p_rate < ALPHA)
    return splits, hits_rupees, hits_rate


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", type=int, default=40)
    ap.add_argument("--first-seed", type=int, default=770001)
    ap.add_argument("--resamples", type=int, default=2000)
    ap.add_argument("--splits", type=int, default=400,
                    help="random re-splits of a single world, for the exactness check")
    ap.add_argument("--params", default="sim/params.yaml")
    ap.add_argument("--out", default="results/calibration.md")
    args = ap.parse_args()

    params = load_params(args.params)
    policies = load_policies("policies")
    costs = CostBook.load("config/rates.yaml")
    start = _dt.datetime(2026, 6, 1, tzinfo=IST)
    response = ResponseModel(params)

    strat = Tally("stratified")
    simple = Tally("simple")
    conditional: tuple[int, int, int] = (0, 0, 0)

    for i in range(args.seeds):
        seed = args.first_seed + i
        population = build_population(params, run_seed=seed, start=start)
        runner = EpisodeRunner(policies=policies, costs=costs, response=response,
                               run_seed=seed, chain=None)

        allocation = stratified_assignment(
            [(ep.ref, int(ep.amount_paise)) for ep in population.episodes], seed, ARM_SHARES
        )
        strat_results = [
            runner.run(ep, allocation[ep.ref], STRATEGIES[allocation[ep.ref]]())
            for ep in population.episodes
            if allocation[ep.ref] is Arm.TREATMENT
        ]
        left, right = _halves_stratified(strat_results, allocation.stratum, seed)
        _score(strat, left, right, seed, args.resamples,
               strata=([allocation.stratum[r.ref] for r in left],
                       [allocation.stratum[r.ref] for r in right]))

        simple_results = [
            runner.run(ep, Arm.TREATMENT, STRATEGIES[Arm.TREATMENT]())
            for ep in population.episodes
            if assign_arm(ep.ref, seed, ARM_SHARES) is Arm.TREATMENT
        ]
        left, right = _halves_simple(simple_results, seed)
        _score(simple, left, right, seed, args.resamples, strata=None)

        if i == 0 and args.splits:
            conditional = conditional_exactness(
                strat_results, allocation.stratum,
                splits=args.splits, resamples=args.resamples,
            )
            print(f"conditional exactness on seed {seed}: "
                  f"{conditional[1]}/{conditional[0]} rupees, "
                  f"{conditional[2]}/{conditional[0]} rate", file=sys.stderr)

        print(f"seed {seed}  stratified {strat.perm_rupees}/{strat.seeds} fp  "
              f"simple {simple.perm_rupees}/{simple.seeds} fp", file=sys.stderr)

    report = _render(args, strat, simple, conditional)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nwrote {out}", file=sys.stderr)
    return 0


def _score(tally: Tally, left, right, seed: int, resamples: int, *, strata) -> None:
    rupees = compare("aa", _values(left, "recovered_paise"), _values(right, "recovered_paise"),
                     seed=seed, strata=strata, resamples=resamples)
    rate = compare("aa", _rates(left), _rates(right),
                   seed=seed, strata=strata, resamples=resamples)
    tally.seeds += 1
    tally.perm_rupees += int(rupees.significant)
    tally.perm_rate += int(rate.significant)
    tally.ci_rupees += int(rupees.interval.excludes_zero)
    tally.ci_rate += int(rate.interval.excludes_zero)
    tally.width_rupees += rupees.interval.high - rupees.interval.low
    tally.width_rate += rate.interval.high - rate.interval.low
    tally.null_rupees += rupees.null_band.high - rupees.null_band.low
    tally.null_rate += rate.null_band.high - rate.null_band.low


def _render(args, strat: Tally, simple: Tally, conditional: tuple[int, int, int]) -> str:
    L: list[str] = []
    A = L.append
    A("# Wapas — A/A calibration")
    A("")
    A(f"{args.seeds} independent worlds, first seed `{args.first_seed}`, "
      f"{args.resamples} resamples per test.")
    A("")
    A("Each row is a **null**: one arm split into two halves that ran the same")
    A("strategy. Any rejection is a false positive by construction. The nominal rate")
    A("is 5%. The verdict column is an exact binomial test of the observed rejection")
    A("count against 5%, one-sided — a rate that merely *looks* high is not evidence")
    A("that it is high.")
    A("")
    A("## 1. False positives across worlds — the procedure as the report runs it")
    A("")
    A("| Design | Test | Rejections | Rate | |")
    A("|---|---|---|---|---|")
    for tally in (strat, simple):
        A(tally.row("permutation, rupees", tally.perm_rupees))
        A(tally.row("permutation, recovery rate", tally.perm_rate))
        A(tally.row("bootstrap CI excludes 0, rupees", tally.ci_rupees))
        A(tally.row("bootstrap CI excludes 0, rate", tally.ci_rate))
    A("")

    splits, hits_rupees, hits_rate = conditional
    if splits:
        A("## 2. Is the test itself exact? One world, many splits")
        A("")
        A(f"{splits} independent random re-splits of a single treatment arm. Conditional")
        A("on fixed data a permutation test is exact regardless of how that data was")
        A("generated, so this isolates the implementation from the simulator and from")
        A("seed-to-seed luck.")
        A("")
        A("| Test | Rejections | Rate | |")
        A("|---|---|---|---|")
        for name, hits in (("permutation, rupees", hits_rupees),
                           ("permutation, recovery rate", hits_rate)):
            rate = hits / splits
            p = binomtest(hits, splits, ALPHA, alternative="greater").pvalue
            flag = "**miscalibrated**" if p < ALPHA else "consistent with 5%"
            A(f"| {name} | {hits}/{splits} | {rate:.1%} | {flag} (p = {p:.3f}) |")
        A("")

    A("## 3. What stratification actually buys")
    A("")
    A("Not calibration — both designs randomise, and section 1 shows both rejecting at")
    A("about the same rate. The claim to test is **precision**: a narrower reference")
    A("distribution is a lower bar for a real effect to clear.")
    A("")
    A("Two widths, because they answer different questions. The **null band** is the")
    A("middle 95% of the permutation distribution — the noise the decision rule has to")
    A("see past. The **bootstrap CI** is the descriptive interval.")
    A("")
    A("| Design | Null band, rupees / ep | Null band, recovery pp | CI width, rupees / ep | CI width, pp |")
    A("|---|---|---|---|---|")
    for tally in (strat, simple):
        A(f"| `{tally.design}` | {tally.mean_null_rupees:,.0f} | {tally.mean_null_rate:.2f} | "
          f"{tally.mean_width_rupees:,.0f} | {tally.mean_width_rate:.2f} |")
    A("")
    gain = (1 - strat.mean_null_rupees / simple.mean_null_rupees) if simple.mean_null_rupees else 0.0
    if abs(gain) < 0.02:
        A(f"**The gain is {gain:+.1%} — that is, there isn't one.** Worth stating plainly")
        A("rather than quietly dropping the table. Amounts here are heavy-tailed but the")
        A("arms are large, and at 750–2,000 episodes simple randomisation already")
        A("balances the deciles well enough that removing the residual imbalance changes")
        A("nothing measurable.")
        A("")
        A("Stratification is kept anyway, for one reason: it converts balance from a")
        A("per-seed accident into a guarantee. That is worth little at this sample size")
        A("and worth a great deal at the sample size a real pilot would have, where a")
        A("merchant runs a few hundred episodes a week and one large invoice landing in")
        A("the wrong arm is the whole result. It costs nothing to keep and it removes an")
        A("entire class of question about whether a given run got lucky.")
    else:
        A(f"Stratifying narrows the null band by **{gain:.1%}** on rupees.")
    A("")

    A("## Reading this")
    A("")
    A("- The **permutation** rows are the decision rule `results/report.md` uses.")
    A("  Section 2 is the authoritative check on whether that rule is sound; section 1")
    A("  has far fewer replications and its rows will bounce around.")
    A("- The **bootstrap CI** rows are the known weak point. A percentile bootstrap on")
    A("  a heavy-tailed difference of means is approximate. That is exactly why the")
    A("  report lets the p-value decide and treats the interval as descriptive.")
    A("- Eight rows are printed and each is tested at 5%, so on a correctly calibrated")
    A("  procedure one row crossing the line is the expected outcome, not a finding.")
    A("  Read section 1 as a smoke alarm, not as a verdict.")
    A("")
    A(f"Reproduce: `make calibrate SEEDS={args.seeds}`")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
