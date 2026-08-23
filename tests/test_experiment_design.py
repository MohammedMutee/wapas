"""Tests for the experiment design itself.

The evaluation is the product. If the allocation is unbalanced, the harness
consults ground truth, or the statistics reject a null they should accept, then
every rupee figure the project reports is decoration. Most of what follows was
written after finding exactly those failures.
"""

from __future__ import annotations

import datetime as _dt
from collections import Counter

import pytest

from eval.run_batch import ARM_SHARES, placebo_halves
from eval.stats import bootstrap_difference, compare, permutation_p
from sim import ResponseModel, build_population, load_params
from wapas.clock import IST
from wapas.domain import Arm, RootCause
from wapas.engine import EpisodeRunner, stratified_assignment
from wapas.llm.costs import CostBook
from wapas.policy import load_policies
from wapas.strategies import NaiveRetry, RulesOnly

SEED = 20260901
START = _dt.datetime(2026, 6, 1, tzinfo=IST)


@pytest.fixture(scope="module")
def world():
    params = load_params()
    return params, build_population(params, run_seed=SEED, start=START)


def make_runner(params) -> EpisodeRunner:
    return EpisodeRunner(
        policies=load_policies("policies"),
        costs=CostBook.load("config/rates.yaml"),
        response=ResponseModel(params),
        run_seed=SEED,
    )


# ── allocation ───────────────────────────────────────────────────────────────


def _episodes(n: int) -> list[tuple[str, int]]:
    # Deliberately heavy-tailed: the failure being guarded against only shows up
    # when a few episodes carry most of the money.
    return [(f"ep_{i:05d}", (i + 1) ** 3) for i in range(n)]


def test_every_arm_gets_the_same_amount_profile():
    """The point of stratifying. Each decile splits by share, to within one."""
    alloc = stratified_assignment(_episodes(5000), SEED, ARM_SHARES)
    per_decile: dict[int, Counter] = {}
    for ref, arm in alloc.arm.items():
        per_decile.setdefault(alloc.stratum[ref], Counter())[str(arm)] += 1

    assert len(per_decile) == 10
    for decile, counts in per_decile.items():
        size = sum(counts.values())
        for arm, share in ARM_SHARES.items():
            assert abs(counts[str(arm)] - size * share) <= 1, (
                f"decile {decile}: {arm} got {counts[str(arm)]} of {size}, "
                f"expected about {size * share:.0f}"
            )


def test_allocation_is_deterministic_and_order_independent():
    episodes = _episodes(1200)
    first = stratified_assignment(episodes, SEED, ARM_SHARES)
    assert first.arm == stratified_assignment(episodes, SEED, ARM_SHARES).arm
    assert first.arm == stratified_assignment(list(reversed(episodes)), SEED, ARM_SHARES).arm
    assert first.arm != stratified_assignment(episodes, SEED + 1, ARM_SHARES).arm


def test_arm_totals_match_the_requested_shares():
    alloc = stratified_assignment(_episodes(5000), SEED, ARM_SHARES)
    counts = Counter(alloc.arm.values())
    for arm, share in ARM_SHARES.items():
        assert abs(counts[arm] - 5000 * share) <= 10


# ── the harness must not know the answer ─────────────────────────────────────


def test_the_action_window_does_not_depend_on_the_true_cause(world):
    """Regression: the harness once derived the action horizon from ground truth.

    ``action_horizon`` was ``DISPOSITIONS[ep.true_cause].default_horizon_hours``,
    so every arm received a cause-aware stopping rule computed from information
    no strategy can see. It flattered the agent — which appeared to know when to
    give up without ever deciding to — and truncated the fixed-ladder baseline
    for causes it cannot identify.

    A strategy that ignores the cause must therefore attempt the same number of
    actions whatever the cause happens to be.
    """
    params, pop = world
    runner = make_runner(params)
    attempts: dict[RootCause, set[int]] = {}
    for ep in pop.episodes:
        if ep.would_self_recover:
            continue  # a payment arriving early legitimately ends the episode
        result = runner.run(ep, Arm.BASELINE_NAIVE, NaiveRetry())
        if result.recovered:
            continue
        attempts.setdefault(ep.true_cause, set()).add(result.actions_taken)

    unfinished = {c: v for c, v in attempts.items() if len(v) > 1}
    assert not unfinished, (
        "a cause-blind strategy ran a different number of actions depending on "
        f"the true cause, so something in the harness is reading it: {unfinished}"
    )


def test_the_action_window_comes_from_policy():
    policies = load_policies("policies")
    assert policies.money.triage.action_window_hours >= 24


# ── externalities ────────────────────────────────────────────────────────────


def test_opt_outs_are_priced_and_kept_separate(world):
    """An opt-out must cost something, and must not be filed as realised spend."""
    params, pop = world
    runner = make_runner(params)
    from wapas.strategies import Blast

    results = [runner.run(ep, Arm.BASELINE_BLAST, Blast()) for ep in pop.episodes[:600]]
    opted = [r for r in results if r.opted_out]
    assert opted, "the blast arm should provoke at least one opt-out"

    for r in opted:
        assert r.externality_paise > 0
        assert r.net_after_externalities_paise < r.net_paise

    for r in results:
        if not (r.opted_out or r.complained or r.disputed):
            assert r.externality_paise == 0
        # Channel spend stays small; externalities must never be folded into it.
        assert r.cost_paise < 10_000


def test_the_externality_scales_with_the_amount_at_risk():
    book = CostBook.load("config/rates.yaml")
    small = book.externalities.opt_out_cost(100_00, is_business=False)
    large = book.externalities.opt_out_cost(10_000_00, is_business=False)
    assert large > small * 90
    assert book.externalities.opt_out_cost(100_00, is_business=True) > small


# ── the statistics ───────────────────────────────────────────────────────────


def test_permutation_accepts_a_true_null():
    values = [float(i % 37) * 100 for i in range(800)]
    p, _ = permutation_p(values[0::2], values[1::2], seed=1, resamples=2000)
    assert p > 0.05


def test_permutation_rejects_a_large_shift():
    base = [float(i % 37) * 100 for i in range(800)]
    shifted = [v + 900 for v in base]
    p, _ = permutation_p(shifted, base, seed=1, resamples=2000)
    assert p < 0.01


def test_a_p_value_is_never_reported_as_zero():
    """10,000 resamples cannot license a claim of p = 0."""
    p, _ = permutation_p([1e9] * 200, [0.0] * 200, seed=1, resamples=1000)
    assert p > 0
    assert p == pytest.approx(1 / 1001, rel=1e-6)


def test_permutation_respects_the_strata_it_is_given():
    """Within-stratum shuffling must not move values between strata."""
    treatment = [10.0] * 50 + [1000.0] * 50
    other = [12.0] * 50 + [1010.0] * 50
    strata = ([0] * 50 + [1] * 50, [0] * 50 + [1] * 50)
    _, band = permutation_p(treatment, other, seed=3, strata=strata, resamples=500)
    # Every permuted difference is bounded by the within-stratum spread (~10),
    # not the between-stratum spread (~1000).
    assert abs(band.low) < 20 and abs(band.high) < 20


def test_statistics_are_deterministic():
    a = [float(i) for i in range(300)]
    b = [float(i) + 3 for i in range(300)]
    first = bootstrap_difference(a, b, seed=7, resamples=500)
    assert first == bootstrap_difference(a, b, seed=7, resamples=500)
    assert first != bootstrap_difference(a, b, seed=8, resamples=500)


def test_a_comparison_of_an_arm_with_itself_claims_nothing():
    values = [float(i % 91) * 250 for i in range(1000)]
    c = compare("self", values, list(values), seed=11, resamples=2000)
    assert not c.significant
    assert c.interval.point == 0.0


# ── the placebo ──────────────────────────────────────────────────────────────


def test_the_placebo_split_is_balanced_and_covers_the_arm(world):
    params, pop = world
    runner = make_runner(params)
    alloc = stratified_assignment(
        [(ep.ref, int(ep.amount_paise)) for ep in pop.episodes], SEED, ARM_SHARES
    )
    treatment = [
        runner.run(ep, Arm.TREATMENT, RulesOnly())
        for ep in pop.episodes if alloc[ep.ref] is Arm.TREATMENT
    ]
    left, right = placebo_halves(treatment, alloc, seed=SEED)

    assert len(left) + len(right) == len(treatment)
    assert {r.ref for r in left}.isdisjoint({r.ref for r in right})
    assert abs(len(left) - len(right)) <= 10
    for stratum in range(10):
        a = sum(1 for r in left if alloc.stratum[r.ref] == stratum)
        b = sum(1 for r in right if alloc.stratum[r.ref] == stratum)
        assert abs(a - b) <= 1
