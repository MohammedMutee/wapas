"""Engine tests.

The properties here are the ones that decide whether the headline number means
anything. Two of them were written *after* the bugs they now guard against —
both bugs inflated our own results, which is the direction that matters.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from sim import ResponseModel, build_population, load_params
from wapas.audit import HashChain, verify_chain
from wapas.clock import IST
from wapas.domain import Arm, RootCause
from wapas.engine import EpisodeRunner, assign_arm
from wapas.llm.costs import CostBook
from wapas.policy import load_policies
from wapas.strategies import Blast, DoNothing, NaiveRetry, RulesOnly

SEED = 20260901
START = _dt.datetime(2026, 6, 1, tzinfo=IST)
SHARES = {Arm.TREATMENT: 0.6, Arm.CONTROL: 0.1, Arm.BASELINE_NAIVE: 0.1,
          Arm.BASELINE_BLAST: 0.1, Arm.BASELINE_RULES: 0.1}


@pytest.fixture(scope="module")
def world():
    params = load_params()
    return params, build_population(params, run_seed=SEED, start=START)


def make_runner(params, chain=None) -> EpisodeRunner:
    return EpisodeRunner(
        policies=load_policies("policies"),
        costs=CostBook.load("config/rates.yaml"),
        response=ResponseModel(params),
        run_seed=SEED,
        chain=chain,
    )


# ── the control arm must actually measure something ──────────────────────────


def test_control_arm_recovers_the_self_recovery_rate(world):
    """Regression: the control arm once recovered 0%.

    Self-recovery was only evaluated at the current clock, and an arm that
    takes no actions never advances its clock. The consequence was that
    "incremental recovery" equalled gross recovery — precisely the over-claim
    the control arm exists to prevent.
    """
    params, pop = world
    runner = make_runner(params)
    sample = pop.episodes[:400]
    results = [runner.run(ep, Arm.CONTROL, DoNothing()) for ep in sample]

    recovered = sum(r.recovered for r in results)
    expected = sum(
        e.would_self_recover
        and e.self_recovery_at is not None
        and e.self_recovery_at <= e.occurred_at + _dt.timedelta(days=30)
        for e in sample
    )
    assert recovered == expected > 0
    assert all(r.self_recovered for r in results if r.recovered), (
        "every control recovery must be marked unattributed"
    )


def test_control_arm_takes_no_actions_and_spends_nothing(world):
    params, pop = world
    runner = make_runner(params)
    results = [runner.run(ep, Arm.CONTROL, DoNothing()) for ep in pop.episodes[:200]]
    assert all(r.actions_taken == 0 and r.contacts_made == 0 for r in results)
    assert sum(r.cost_paise for r in results) == 0


# ── baselines must be real competitors, not strawmen ─────────────────────────


def test_the_naive_baseline_can_actually_retry(world):
    """Regression: `unknown` sat in never_retry_causes, so any strategy that
    does not diagnose was reduced to inaction — a rigged comparison."""
    params, pop = world
    runner = make_runner(params)
    payment_eps = [e for e in pop.episodes if e.surface == "payment"][:150]
    results = [runner.run(ep, Arm.BASELINE_NAIVE, NaiveRetry()) for ep in payment_eps]
    assert sum(r.retries for r in results) > 0, "the industry-default baseline must act"


def test_the_naive_baseline_beats_doing_nothing(world):
    """If it did not, we would be comparing against a strawman."""
    params, pop = world
    runner = make_runner(params)
    sample = [e for e in pop.episodes if e.surface == "payment"][:400]
    naive = sum(r.recovered for r in (runner.run(e, Arm.BASELINE_NAIVE, NaiveRetry())
                                      for e in sample))
    nothing = sum(r.recovered for r in (runner.run(e, Arm.CONTROL, DoNothing())
                                        for e in sample))
    assert naive > nothing


# ── invariants that hold whatever the strategy proposes ──────────────────────


@pytest.mark.parametrize("strategy_cls", [RulesOnly, NaiveRetry, Blast])
def test_policy_bounds_hold_for_every_strategy(world, strategy_cls):
    """Even the deliberately reckless arm cannot exceed the caps."""
    params, pop = world
    runner = make_runner(params)
    caps = load_policies("policies")
    for ep in pop.episodes[:250]:
        r = runner.run(ep, Arm.TREATMENT, strategy_cls())
        assert r.contacts_made <= caps.contact.frequency_caps.contacts_per_episode
        assert r.retries <= caps.money.money_actions.max_retries_per_payment
        assert r.actions_taken <= 24


def test_a_risk_decline_is_never_retried(world):
    params, pop = world
    runner = make_runner(params)
    declines = [e for e in pop.episodes if e.true_cause is RootCause.RISK_DECLINED][:40]
    assert declines
    for ep in declines:
        r = runner.run(ep, Arm.TREATMENT, RulesOnly())
        assert r.retries == 0, "rail-shopping around a risk decline is abuse"


def test_an_expired_card_is_never_retried(world):
    params, pop = world
    runner = make_runner(params)
    expired = [e for e in pop.episodes
               if e.true_cause is RootCause.CARD_EXPIRED_OR_INVALID][:40]
    assert expired
    assert all(runner.run(ep, Arm.TREATMENT, RulesOnly()).retries == 0 for ep in expired)


def test_every_episode_reaches_a_terminal_state(world):
    params, pop = world
    runner = make_runner(params)
    from wapas.domain import TERMINAL_STATES
    for ep in pop.episodes[:300]:
        r = runner.run(ep, Arm.TREATMENT, RulesOnly())
        assert r.state in TERMINAL_STATES, f"{ep.ref} ended in {r.state}"


def test_recovered_never_exceeds_the_amount_at_risk(world):
    params, pop = world
    runner = make_runner(params)
    for ep in pop.episodes[:300]:
        r = runner.run(ep, Arm.TREATMENT, RulesOnly())
        assert r.recovered_paise <= ep.amount_paise


# ── reproducibility ──────────────────────────────────────────────────────────


def test_a_run_is_reproducible(world):
    params, pop = world
    sample = pop.episodes[:150]
    a = [make_runner(params).run(e, Arm.TREATMENT, RulesOnly()) for e in sample]
    b = [make_runner(params).run(e, Arm.TREATMENT, RulesOnly()) for e in sample]
    assert [(x.state, x.recovered_paise, x.cost_paise) for x in a] == \
           [(y.state, y.recovered_paise, y.cost_paise) for y in b]


def test_arm_assignment_is_stable_and_balanced():
    refs = [f"ep_{i:05d}" for i in range(4000)]
    first = [assign_arm(r, SEED, SHARES) for r in refs]
    assert first == [assign_arm(r, SEED, SHARES) for r in refs]
    share = sum(a is Arm.CONTROL for a in first) / len(first)
    assert 0.08 < share < 0.12


def test_the_audit_chain_survives_a_full_run(world):
    params, pop = world
    chain = HashChain(salt="engine-test")
    runner = make_runner(params, chain=chain)
    for ep in pop.episodes[:120]:
        runner.run(ep, Arm.TREATMENT, RulesOnly())
    assert len(chain) > 300
    result = verify_chain(chain)
    assert result.ok, str(result)


def test_denials_are_recorded_not_discarded(world):
    """The count of blocked actions is the evidence the gate is load-bearing."""
    params, pop = world
    runner = make_runner(params)
    total = sum(runner.run(ep, Arm.BASELINE_BLAST, Blast()).denials
                for ep in pop.episodes[:200])
    assert total > 0
