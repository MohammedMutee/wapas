"""Synthetic counterparties.

Each carries **latent traits the agent never sees**: when their salary lands,
how many contacts they tolerate before opting out, which channel they actually
read, and — most importantly — whether they would have paid anyway.

The agent observes only what it has tried and what happened. That asymmetry is
the point: an agent that could read ``liquidity_refresh_day`` would score
brilliantly and prove nothing.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from wapas.domain import Channel, RootCause, Surface
from wapas.money import Paise, rupees_to_paise

from .params import SimParams
from .rng import Rng
from .signals import draw_signal


@dataclass(frozen=True, slots=True)
class Consumer:
    """A retail customer. All fields here are latent."""

    ref: str
    liquidity_refresh_day: int
    responsiveness: float
    annoyance_threshold: int
    price_sensitivity: float
    channel_preference: Channel
    self_recovery_rate: float
    """Probability of paying with no intervention. The reason a control arm exists."""

    is_business: bool = False


@dataclass(frozen=True, slots=True)
class B2BBuyer:
    """A business buyer. All fields here are latent."""

    ref: str
    persona: str
    """``prompt_payer`` | ``cash_crunched`` | ``disputer`` | ``ghost``"""
    promise_keep_rate: float
    dispute_propensity: float
    days_late_baseline: float
    self_recovery_rate: float
    responsiveness: float
    annoyance_threshold: int
    price_sensitivity: float
    channel_preference: Channel

    is_business: bool = True


Counterparty = Consumer | B2BBuyer


@dataclass(frozen=True, slots=True)
class SeededEpisode:
    """One episode's ground truth, as the simulator knows it.

    ``true_cause`` is what the diagnosis step is scored against, and
    ``would_self_recover`` is the counterfactual the control arm measures
    directly.
    """

    ref: str
    surface: Surface
    counterparty: Counterparty
    amount_paise: Paise
    true_cause: RootCause
    rail: str
    occurred_at: _dt.datetime
    error_code: str
    error_description: str
    error_source: str
    error_step: str
    issuer_down_until: _dt.datetime | None
    would_self_recover: bool
    self_recovery_at: _dt.datetime | None
    seed: int
    signal_established: bool = True
    """Whether this wording predates the evaluation window.

    False marks text no resolved history can contain — a new acquirer, a bank
    changing its phrasing. Ground truth about the *task*, never visible to a
    strategy, and the axis the report splits accuracy on: a lookup table is
    optimal on established wordings and helpless on new ones.
    """
    signal_informative: bool = True
    """Whether the error text can identify the cause at all.

    Ground truth about the *task*, not about the answer. Recorded so the report
    can split diagnosis accuracy by whether the question was answerable, which
    is the only place a model can be expected to beat a keyword table. No
    strategy ever sees it."""


@dataclass
class Population:
    """A generated world: counterparties, episodes, and the outage timeline."""

    params: SimParams
    run_seed: int
    consumers: list[Consumer] = field(default_factory=list)
    buyers: list[B2BBuyer] = field(default_factory=list)
    episodes: list[SeededEpisode] = field(default_factory=list)
    outages: list[tuple[_dt.datetime, _dt.datetime]] = field(default_factory=list)

    def issuer_down_at(self, moment: _dt.datetime) -> bool:
        return any(start <= moment < end for start, end in self.outages)


# Superseded by ``sim.signals``. Kept as the canonical, unambiguous phrasing of
# each cause — useful in tests and in documentation — but no longer what
# episodes present, because one fixed string per cause turned diagnosis into a
# lookup table. See ``sim/signals.py`` for why that mattered.
ERROR_TEMPLATES: dict[RootCause, tuple[str, str, str, str]] = {
    RootCause.INSUFFICIENT_FUNDS: (
        "BAD_REQUEST_ERROR",
        "Your card has insufficient balance to complete this payment",
        "issuer", "authorization",
    ),
    RootCause.AUTHENTICATION_FAILED: (
        "GATEWAY_ERROR",
        "Customer did not complete 3DS authentication within the time limit",
        "customer", "authentication",
    ),
    RootCause.ISSUER_DOWN: (
        "GATEWAY_ERROR",
        "The issuing bank is not reachable at the moment. Please retry shortly",
        "issuer", "authorization",
    ),
    RootCause.TECHNICAL_TIMEOUT: (
        "GATEWAY_ERROR",
        "Payment processing timed out; final status unknown",
        "gateway", "authorization",
    ),
    RootCause.CARD_EXPIRED_OR_INVALID: (
        "BAD_REQUEST_ERROR",
        "The card has expired. Please use a different payment method",
        "issuer", "authorization",
    ),
    RootCause.LIMIT_EXCEEDED: (
        "BAD_REQUEST_ERROR",
        "Transaction amount exceeds the per-transaction limit set by the bank",
        "issuer", "authorization",
    ),
    RootCause.RISK_DECLINED: (
        "BAD_REQUEST_ERROR",
        "Payment declined by the issuing bank risk engine",
        "issuer", "authorization",
    ),
    RootCause.CUSTOMER_CANCELLED: (
        "BAD_REQUEST_ERROR",
        "Payment was cancelled by the customer on the bank page",
        "customer", "authentication",
    ),
    RootCause.MANDATE_REVOKED: (
        "BAD_REQUEST_ERROR",
        "The mandate for this subscription has been revoked by the customer",
        "customer", "authorization",
    ),
    RootCause.MANDATE_INSUFFICIENT: (
        "BAD_REQUEST_ERROR",
        "Auto-debit failed: insufficient balance in the mandated account",
        "issuer", "authorization",
    ),
    RootCause.INVOICE_FORGOTTEN: ("", "Invoice past due date, no response recorded", "", ""),
    RootCause.INVOICE_CASH_CRUNCH: ("", "Invoice past due date, buyer reports cash constraints", "", ""),
    RootCause.INVOICE_DISPUTED: ("", "Invoice past due date, buyer disputes the line items", "", ""),
}

B2B_PERSONA_CAUSE = {
    "prompt_payer": RootCause.INVOICE_FORGOTTEN,
    "cash_crunched": RootCause.INVOICE_CASH_CRUNCH,
    "disputer": RootCause.INVOICE_DISPUTED,
    "ghost": RootCause.INVOICE_FORGOTTEN,
}


def _amount(rng: Rng, p: SimParams) -> Paise:
    a = p.amounts
    rupees = min(max(rng._r.lognormvariate(a.mu, a.sigma), a.min_rupees), a.max_rupees)
    return rupees_to_paise(round(rupees, 2))


def _rail_for(rng: Rng, p: SimParams, cause: RootCause) -> str:
    weights = {
        rail: affinities.get(str(cause), affinities.get("default", 0.1))
        for rail, affinities in p.rails.items()
    }
    return rng.weighted(weights)


def _make_consumer(rng: Rng, p: SimParams, ref: str) -> Consumer:
    c = p.consumer
    return Consumer(
        ref=ref,
        liquidity_refresh_day=int(rng.categorical(c.liquidity_refresh_day)),
        responsiveness=rng.draw(c.responsiveness),
        annoyance_threshold=max(1, int(rng.draw(c.annoyance_threshold))),
        price_sensitivity=rng.draw(c.price_sensitivity),
        channel_preference=Channel(rng.categorical(c.channel_preference)),
        self_recovery_rate=rng.draw(c.self_recovery_rate),
    )


def _make_buyer(rng: Rng, p: SimParams, ref: str) -> B2BBuyer:
    b, c = p.b2b_buyer, p.consumer
    return B2BBuyer(
        ref=ref,
        persona=str(rng.categorical(b.persona)),
        promise_keep_rate=rng.draw(b.promise_keep_rate),
        dispute_propensity=rng.draw(b.dispute_propensity),
        days_late_baseline=rng.draw(b.days_late_baseline),
        self_recovery_rate=rng.draw(b.self_recovery_rate),
        responsiveness=rng.draw(c.responsiveness),
        annoyance_threshold=max(1, int(rng.draw(c.annoyance_threshold))),
        price_sensitivity=rng.draw(c.price_sensitivity),
        channel_preference=Channel(rng.categorical(c.channel_preference)),
    )


def _outages(rng: Rng, p: SimParams, start: _dt.datetime) -> list[tuple[_dt.datetime, _dt.datetime]]:
    """Bursty issuer downtime.

    Modelled as correlated bursts rather than i.i.d. draws. With independent
    draws a fixed retry ladder recovers nearly as well as a cause-aware one,
    and the timing intelligence looks worthless — which would be an artefact of
    the simulator, not a finding about the agent.
    """
    o = p.issuer_outages
    out = []
    for i in range(o.bursts_per_90_days):
        r = rng.child("outage", i)
        offset = r.uniform(0, p.horizon_days * 24 * 60)
        dur = r.randint(o.burst_duration_minutes["min"], o.burst_duration_minutes["max"])
        begin = start + _dt.timedelta(minutes=offset)
        out.append((begin, begin + _dt.timedelta(minutes=dur)))
    return sorted(out)


def build_population(
    params: SimParams,
    *,
    run_seed: int,
    start: _dt.datetime,
    established_signals_only: bool = False,
) -> Population:
    """Generate the whole world from a single seed.

    ``established_signals_only`` builds a *resolved history* population: the
    same world, but restricted to error wordings the merchant has seen before.
    The evaluation population is built without it, so roughly a quarter of its
    informative episodes carry text no history contains. See ``sim/signals.py``
    for why that distinction is the whole experiment.
    """
    root = Rng(run_seed, "population")
    pop = Population(params=params, run_seed=run_seed, outages=_outages(root.child("outages"), params, start))

    v = params.volumes
    horizon_min = params.horizon_days * 24 * 60

    # ── Surface A: failed payments ───────────────────────────────────────────
    for i in range(v.payment_episodes):
        r = root.child("payment", i)
        consumer = _make_consumer(r.child("traits"), params, f"cons_{i:05d}")
        pop.consumers.append(consumer)

        cause = RootCause(r.child("cause").weighted(params.failure_causes))
        occurred = start + _dt.timedelta(minutes=r.child("when").uniform(0, horizon_min))

        # Issuer-down failures must actually coincide with an outage, otherwise
        # the diagnosis task is unfairly hard and the timing model unfairly easy.
        down_until = None
        if cause is RootCause.ISSUER_DOWN and pop.outages:
            begin, end = r.child("pick_outage").choice(pop.outages)
            occurred = begin + _dt.timedelta(
                minutes=r.child("in_outage").uniform(0, max(1, (end - begin).total_seconds() / 60))
            )
            down_until = end

        signal = draw_signal(r.child("signal"), cause,
                             uninformative_share=params.signal_noise.uninformative_share,
                             established_only=established_signals_only)
        code, desc, source, step = (signal.code, signal.description,
                                    signal.source, signal.step)
        sr = r.child("selfrec")
        self_recovers = sr.chance(consumer.self_recovery_rate) and cause not in {
            RootCause.RISK_DECLINED, RootCause.CARD_EXPIRED_OR_INVALID,
        }
        pop.episodes.append(SeededEpisode(
            ref=f"pay_{i:05d}", surface=Surface.PAYMENT, counterparty=consumer,
            amount_paise=_amount(r.child("amt"), params), true_cause=cause,
            rail=_rail_for(r.child("rail"), params, cause), occurred_at=occurred,
            error_code=code, error_description=desc, error_source=source, error_step=step,
            issuer_down_until=down_until, signal_informative=signal.informative,
            signal_established=signal.established,
            would_self_recover=self_recovers,
            self_recovery_at=(
                occurred + _dt.timedelta(hours=sr.uniform(2, 120)) if self_recovers else None
            ),
            seed=r.seed,
        ))

    # ── Surface B: mandates ──────────────────────────────────────────────────
    for i in range(v.mandate_episodes):
        r = root.child("mandate", i)
        consumer = _make_consumer(r.child("traits"), params, f"sub_{i:05d}")
        pop.consumers.append(consumer)
        cause = (RootCause.MANDATE_REVOKED if r.child("cause").chance(0.3)
                 else RootCause.MANDATE_INSUFFICIENT)
        signal = draw_signal(r.child("signal"), cause,
                             uninformative_share=params.signal_noise.uninformative_share,
                             established_only=established_signals_only)
        code, desc, source, step = (signal.code, signal.description,
                                    signal.source, signal.step)
        occurred = start + _dt.timedelta(minutes=r.child("when").uniform(0, horizon_min))
        sr = r.child("selfrec")
        self_recovers = cause is RootCause.MANDATE_INSUFFICIENT and sr.chance(
            consumer.self_recovery_rate * 0.6
        )
        pop.episodes.append(SeededEpisode(
            ref=f"man_{i:05d}", surface=Surface.MANDATE, counterparty=consumer,
            amount_paise=_amount(r.child("amt"), params), true_cause=cause,
            rail="emandate" if r.child("rail").chance(0.5) else "upi",
            occurred_at=occurred, error_code=code, error_description=desc,
            error_source=source, error_step=step, issuer_down_until=None,
            signal_informative=signal.informative,
            signal_established=signal.established,
            would_self_recover=self_recovers,
            self_recovery_at=(
                occurred + _dt.timedelta(days=sr.uniform(1, 20)) if self_recovers else None
            ),
            seed=r.seed,
        ))

    # ── Surface C: receivables ───────────────────────────────────────────────
    for i in range(v.receivable_episodes):
        r = root.child("receivable", i)
        buyer = _make_buyer(r.child("traits"), params, f"biz_{i:05d}")
        pop.buyers.append(buyer)
        cause = B2B_PERSONA_CAUSE[buyer.persona]
        signal = draw_signal(r.child("signal"), cause,
                             uninformative_share=params.signal_noise.uninformative_share,
                             established_only=established_signals_only)
        code, desc, source, step = (signal.code, signal.description,
                                    signal.source, signal.step)
        occurred = start + _dt.timedelta(minutes=r.child("when").uniform(0, horizon_min))
        sr = r.child("selfrec")
        self_recovers = buyer.persona != "ghost" and sr.chance(buyer.self_recovery_rate)
        pop.episodes.append(SeededEpisode(
            ref=f"inv_{i:05d}", surface=Surface.RECEIVABLE, counterparty=buyer,
            amount_paise=_amount(r.child("amt"), params) * 4,
            true_cause=cause, rail="bank_transfer", occurred_at=occurred,
            error_code=code, error_description=desc, error_source=source, error_step=step,
            issuer_down_until=None, signal_informative=signal.informative,
            signal_established=signal.established,
            would_self_recover=self_recovers,
            self_recovery_at=(
                occurred + _dt.timedelta(days=sr.uniform(2, 45)) if self_recovers else None
            ),
            seed=r.seed,
        ))

    pop.episodes.sort(key=lambda e: (e.occurred_at, e.ref))
    return pop
