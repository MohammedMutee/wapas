"""Tests for the decision not to act.

The feature ships disabled because measurement said it costs 2% of net at our
own externality estimate (`results/triage.md`). These pin the machinery anyway:
a switch that is off today should still be correct when someone turns it on,
and the study that says to leave it off is only trustworthy if the thing it
measured works.
"""

from __future__ import annotations

import datetime as _dt

from wapas.clock import IST
from wapas.domain import RootCause, Surface
from wapas.llm.costs import CostBook
from wapas.money import Paise
from wapas.triage import RecoverabilityScorer, amount_band, triage

COSTS = CostBook.load("config/rates.yaml")
NOW = _dt.datetime(2026, 6, 1, tzinfo=IST)


class _Result:
    """Minimal stand-in for an EpisodeResult the scorer can learn from."""

    def __init__(self, cause, surface, amount, recovered):
        self.true_cause = cause
        self.surface = str(surface)
        self.amount_paise = Paise(amount)
        self.recovered = recovered


def _scorer(recovered: int, total: int, cause=RootCause.INSUFFICIENT_FUNDS,
            amount: int = 250_000) -> RecoverabilityScorer:
    rows = [_Result(cause, Surface.PAYMENT, amount, i < recovered) for i in range(total)]
    return RecoverabilityScorer.from_results(rows)


# ── the probability ──────────────────────────────────────────────────────────


def test_the_estimate_is_the_observed_rate():
    """Calibrated by construction is the whole reason for this design."""
    scorer = _scorer(30, 100)
    got = scorer.estimate(cause=RootCause.INSUFFICIENT_FUNDS,
                          surface=Surface.PAYMENT, amount=Paise(250_000))
    assert got.probability == 0.30
    assert got.level == "cause_surface_band"


def test_a_thin_cell_backs_off_rather_than_believing_itself():
    """Four episodes in a cell is not evidence, and acting on it as though it
    were is how a triage step starts refusing to chase real money."""
    scorer = _scorer(4, 4)  # 100% recovery on four episodes
    got = scorer.estimate(cause=RootCause.INSUFFICIENT_FUNDS,
                          surface=Surface.PAYMENT, amount=Paise(250_000))
    assert got.level in {"cause", "global"}, "a 4-episode cell was treated as evidence"


def test_an_unseen_cause_falls_all_the_way_back():
    scorer = _scorer(50, 100)
    got = scorer.estimate(cause=RootCause.INVOICE_DISPUTED,
                          surface=Surface.RECEIVABLE, amount=Paise(9_000_000))
    assert got.level == "global"
    assert not got.confident


def test_calibration_error_is_zero_on_its_own_training_data():
    scorer = _scorer(30, 100)
    rows = [_Result(RootCause.INSUFFICIENT_FUNDS, Surface.PAYMENT, 250_000, i < 30)
            for i in range(100)]
    assert scorer.calibration_error(rows) < 1e-9


def test_amount_bands_are_stable():
    assert amount_band(Paise(49_900)) == "<500"
    assert amount_band(Paise(250_000)) == "2k-10k"
    assert amount_band(Paise(9_000_000)) == "50k+"


# ── the decision ─────────────────────────────────────────────────────────────


def test_an_unrecoverable_cause_is_never_worked():
    decision = triage(
        cause=RootCause.RISK_DECLINED, surface=Surface.PAYMENT,
        amount=Paise(500_000), is_business=False, scorer=_scorer(50, 100),
        costs=COSTS, ev_floor_paise=500,
    )
    assert not decision.work and "not recoverable" in decision.reason


def test_a_likely_recovery_is_worked():
    decision = triage(
        cause=RootCause.INSUFFICIENT_FUNDS, surface=Surface.PAYMENT,
        amount=Paise(500_000), is_business=False,
        scorer=_scorer(80, 100), costs=COSTS, ev_floor_paise=500,
    )
    assert decision.work and decision.expected_value_paise > 0


def test_a_hopeless_episode_is_left_alone():
    decision = triage(
        cause=RootCause.INSUFFICIENT_FUNDS, surface=Surface.PAYMENT,
        amount=Paise(500_000), is_business=False,
        scorer=_scorer(1, 200), costs=COSTS, ev_floor_paise=500, planned_contacts=4,
    )
    assert not decision.work
    assert decision.expected_harm_paise > decision.expected_recovery_paise


def test_the_harm_term_is_what_makes_the_decision_bite():
    """Revenue alone clears any sane floor, which is why the floor sat unused.

    A 2% chance on a large invoice is still worth more than a few paise of SMS.
    The decision only becomes real once the expected cost of losing the customer
    is on the same side of the ledger.
    """
    common = dict(cause=RootCause.INSUFFICIENT_FUNDS, surface=Surface.PAYMENT,
                  amount=Paise(500_000), is_business=False,
                  scorer=_scorer(2, 200), costs=COSTS, ev_floor_paise=500)
    with_harm = triage(**common, planned_contacts=4)
    assert not with_harm.work
    revenue_only = with_harm.expected_recovery_paise - with_harm.expected_cost_paise
    assert revenue_only > 500, "revenue alone would have cleared the floor"


def test_more_planned_contacts_raise_the_bar():
    common = dict(cause=RootCause.INSUFFICIENT_FUNDS, surface=Surface.PAYMENT,
                  amount=Paise(400_000), is_business=False,
                  scorer=_scorer(12, 200), costs=COSTS, ev_floor_paise=500)
    assert triage(**common, planned_contacts=1).expected_harm_paise < \
           triage(**common, planned_contacts=5).expected_harm_paise


def test_a_decision_always_explains_itself():
    for scorer in (_scorer(90, 100), _scorer(1, 200)):
        decision = triage(
            cause=RootCause.INSUFFICIENT_FUNDS, surface=Surface.PAYMENT,
            amount=Paise(500_000), is_business=False, scorer=scorer,
            costs=COSTS, ev_floor_paise=500,
        )
        assert decision.reason and decision.describe()
