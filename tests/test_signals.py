"""Tests for the failure signals the simulator emits, and for reading them.

The point of ``sim/signals.py`` is that diagnosis should be a judgement rather
than a lookup. These assert that it stayed that way — a later change that
quietly makes every cause identifiable again would inflate every accuracy
number in the report without anyone noticing.
"""

from __future__ import annotations

import datetime as _dt
from collections import Counter

from sim import build_population, load_params
from sim.rng import Rng
from sim.signals import UNINFORMATIVE, VARIANTS, draw_signal
from wapas.clock import IST
from wapas.domain import RootCause, Surface
from wapas.money import Paise
from wapas.strategies import RulesOnly
from wapas.strategies.base import StrategyContext

NOW = _dt.datetime(2026, 6, 1, tzinfo=IST)


def test_every_generated_cause_has_more_than_one_phrasing():
    """One string per cause is a lookup table, not a diagnosis problem."""
    params = load_params()
    generated = {RootCause(name) for name in params.failure_causes}
    for cause in generated:
        assert len(VARIANTS[cause]) >= 3, f"{cause} has too few phrasings to be hard"


def test_the_uninformative_share_matches_the_published_parameter():
    rng = Rng(1, "signals")
    drawn = [draw_signal(rng.child(i), RootCause.INSUFFICIENT_FUNDS,
                         uninformative_share=0.18)
             for i in range(3000)]
    share = sum(1 for s in drawn if not s.informative) / len(drawn)
    assert 0.15 < share < 0.21, f"published 0.18, generated {share:.3f}"


def test_receivable_signals_carry_no_gateway_code():
    """There is no acquirer on the receivables surface. Only what the buyer said."""
    for cause in (RootCause.INVOICE_FORGOTTEN, RootCause.INVOICE_CASH_CRUNCH,
                  RootCause.INVOICE_DISPUTED):
        for signal in VARIANTS[cause]:
            assert signal.code == ""


def test_uninformative_text_is_genuinely_uninformative():
    """No uninformative string may contain a word that identifies a cause.

    If one did, the 18% of episodes meant to be undiagnosable would be quietly
    diagnosable, and diagnosis accuracy would rise for a reason unrelated to
    diagnosis.
    """
    telltales = ("insufficient", "expired", "3ds", "otp", "mandate", "limit",
                 "fraud", "cancel", "timeout", "risk", "51", "54", "91")
    for signal in UNINFORMATIVE:
        text = f"{signal.description} {signal.code}".lower()
        for word in telltales:
            assert word not in text, f"{signal.description!r} gives away {word!r}"


def test_the_classifier_reads_iso_response_codes():
    """The codes a real integration receives when it receives anything."""
    rules = RulesOnly()
    for text, expected in (
        ("Issuer response 51: NOT SUFFICIENT FUNDS", RootCause.INSUFFICIENT_FUNDS),
        ("Issuer response 54: EXPIRED CARD", RootCause.CARD_EXPIRED_OR_INVALID),
        ("Issuer response 91: ISSUER OR SWITCH INOPERATIVE", RootCause.ISSUER_DOWN),
        ("Issuer response 61: EXCEEDS WITHDRAWAL AMOUNT LIMIT", RootCause.LIMIT_EXCEEDED),
        ("Issuer response 59: SUSPECTED FRAUD", RootCause.RISK_DECLINED),
    ):
        ctx = StrategyContext(
            opened_at=NOW, now=NOW, surface=Surface.PAYMENT, amount_paise=Paise(100_000),
            rail="card", error_code="BAD_REQUEST_ERROR", error_description=text,
            error_source="issuer", error_step="authorization", attempt_no=1,
            is_business=False,
        )
        assert rules.diagnose(ctx).root_cause is expected, text


def test_a_bare_number_in_prose_is_not_read_as_a_response_code():
    """"51" in an amount is not a decline code, and guessing so would be worse
    than not looking."""
    rules = RulesOnly()
    ctx = StrategyContext(
        opened_at=NOW, now=NOW, surface=Surface.PAYMENT, amount_paise=Paise(100_000),
        rail="card", error_code="", error_description="Retry attempt 51 of the batch",
        error_source="", error_step="", attempt_no=1, is_business=False,
    )
    assert rules.diagnose(ctx).root_cause is not RootCause.INSUFFICIENT_FUNDS


def test_an_uninformative_signal_produces_a_low_confidence_answer():
    rules = RulesOnly()
    ctx = StrategyContext(
        opened_at=NOW, now=NOW, surface=Surface.PAYMENT, amount_paise=Paise(100_000),
        rail="upi", error_code="GATEWAY_ERROR", error_description="Transaction declined",
        error_source="", error_step="", attempt_no=1, is_business=False,
    )
    diagnosis = rules.diagnose(ctx)
    assert diagnosis.confidence <= 0.5, (
        "a classifier that is confident about a content-free error string is "
        "the thing that causes forbidden retries"
    )


def test_the_population_actually_uses_the_variants():
    params = load_params()
    population = build_population(params, run_seed=4242, start=NOW)
    texts = Counter(e.error_description for e in population.episodes)
    assert len(texts) > 40, f"only {len(texts)} distinct failure strings in the world"
    assert max(texts.values()) / len(population.episodes) < 0.15, (
        "one phrasing dominates; the variants are not being sampled"
    )
