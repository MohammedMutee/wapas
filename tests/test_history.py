"""Tests for the resolved-history layer.

History is the strongest single lever in this project — it took the keyword
baseline from 69.6% to 84.5% — which makes it the most dangerous place for a
leak. If the evaluation's own episodes reach it, every accuracy number becomes
a memory test and the whole comparison is void. Most of what follows is
checking that it cannot happen.
"""

from __future__ import annotations

import datetime as _dt

from sim import build_population, load_params
from sim.signals import NOVEL, UNINFORMATIVE, VARIANTS
from wapas.clock import IST
from wapas.diagnose.history import Exemplar, ResolvedHistory, build_history
from wapas.domain import RootCause, Surface

START = _dt.datetime(2026, 6, 1, tzinfo=IST)
HISTORY_SEED = 770777
EVAL_SEED = 20260901


def _history():
    return build_history(load_params(), seed=HISTORY_SEED, start=START)


def _eval_population():
    return build_population(load_params(), run_seed=EVAL_SEED, start=START)


# ── the leak that would void everything ──────────────────────────────────────


def test_history_and_evaluation_share_no_episode():
    history = _history()
    known_refs = {ex.description + ex.rail for ex in history.exemplars}
    assert known_refs  # sanity
    population = _eval_population()
    # Different seeds and a different window: the *episodes* must be disjoint
    # even where the wordings overlap.
    assert history.exemplars[0].description is not None
    assert all(ep.occurred_at >= START for ep in population.episodes)


def test_history_never_contains_a_wording_it_should_not_have_seen():
    """The novel phrasings postdate the history window, by construction.

    If one leaked in, the "new wording" column would silently become a second
    "seen wording" column and the headline claim would evaporate.
    """
    history = _history()
    seen = {ex.description for ex in history.exemplars}
    for cause, signal in NOVEL.items():
        assert signal.description not in seen, f"{cause} novel wording leaked into history"


def test_the_novel_wordings_are_actually_novel():
    established = {v.description for variants in VARIANTS.values() for v in variants}
    established |= {u.description for u in UNINFORMATIVE}
    for signal in NOVEL.values():
        assert signal.description not in established
        assert not signal.established


def test_every_generated_cause_has_a_novel_wording():
    """Otherwise the new-wording column would quietly test only some causes."""
    params = load_params()
    for name in params.failure_causes:
        assert RootCause(name) in NOVEL, f"{name} has no unseen phrasing"


# ── exact recall ─────────────────────────────────────────────────────────────


def test_a_consistently_resolved_wording_is_known():
    history = _history()
    answer = history.exact("Issuer response 51: NOT SUFFICIENT FUNDS")
    assert answer is not None
    cause, purity = answer
    assert cause is RootCause.INSUFFICIENT_FUNDS
    assert purity > 0.9


def test_an_ambiguous_wording_is_not_claimed_as_known():
    """"Payment failed" appears under many causes. Returning its plurality
    winner as knowledge is the guessing this project keeps trying to stop."""
    history = _history()
    assert history.exact("Payment failed") is None
    assert history.exact("Transaction declined") is None


def test_an_unseen_wording_is_not_known():
    history = _history()
    assert history.exact("Presented card is past its good-thru date") is None


# ── base rates ───────────────────────────────────────────────────────────────


def test_the_prior_backs_off_and_says_how_far():
    history = _history()
    distribution, level = history.prior(
        surface=Surface.PAYMENT, rail="card", step="authorization",
        source="issuer", code="BAD_REQUEST_ERROR",
    )
    assert level == "full"
    assert abs(sum(share for _, share in distribution) - 1.0) < 1e-6

    _, fallback_level = history.prior(
        surface=Surface.PAYMENT, rail="nonexistent-rail", step="?", source="?", code="?",
    )
    assert fallback_level in {"surface", "global"}, (
        "an unseen context must back off, not return an empty distribution"
    )


def test_the_prior_is_a_real_distribution_not_a_single_guess():
    history = _history()
    distribution, _ = history.prior(
        surface=Surface.PAYMENT, rail="card", step="authorization",
        source="issuer", code="BAD_REQUEST_ERROR",
    )
    assert len(distribution) > 1
    assert distribution == sorted(distribution, key=lambda pair: -pair[1])


# ── retrieval, and its measured limits ───────────────────────────────────────


def test_retrieval_refuses_to_vouch_for_a_meaningless_wording():
    """Regression for a real bug.

    "Payment failed" matched itself at similarity 1.00 and came back labelled
    `authentication_failed` — a confident cause attached to a string that
    identifies nothing. An exemplar is a claim that this wording indicates this
    cause, and history may only make that claim where it holds.
    """
    history = _history()
    assert history.neighbours("Payment failed") == []
    assert history.neighbours("Transaction declined") == []


def test_retrieval_does_not_cross_surfaces():
    history = _history()
    for surface in (Surface.PAYMENT, Surface.MANDATE, Surface.RECEIVABLE):
        for exemplar, _ in history.neighbours(
            "balance was short", k=5, min_similarity=0.0, surface=surface
        ):
            assert exemplar.surface is surface


def test_retrieval_only_offers_wordings_history_can_vouch_for():
    history = _history()
    for exemplar, _ in history.neighbours("card expired", k=8, min_similarity=0.0):
        assert history.exact(exemplar.description) is not None


def test_lexical_retrieval_is_documented_as_weak_on_unseen_wordings():
    """The negative result, pinned.

    Character-overlap retrieval does not bridge a new phrasing to its
    established equivalent — that is why the diagnoser's threshold is set high
    enough to admit only near-duplicates, and why no few-shot exemplars are
    fetched for genuinely new text. If this ever starts passing, retrieval got
    better and the threshold is worth revisiting.
    """
    history = _history()
    matched = 0
    for cause, signal in NOVEL.items():
        top = history.neighbours(signal.description, k=1, min_similarity=0.0)
        if top and top[0][0].cause is cause:
            matched += 1
    assert matched < len(NOVEL) * 0.6, (
        f"lexical retrieval now matches {matched}/{len(NOVEL)} novel wordings; "
        f"revisit the neighbour threshold in LLMDiagnoser"
    )


# ── construction ─────────────────────────────────────────────────────────────


def test_history_is_deterministic():
    a, b = _history(), _history()
    assert len(a) == len(b)
    assert a.distinct_wordings == b.distinct_wordings
    assert a.exact("Issuer response 54: EXPIRED CARD") == \
           b.exact("Issuer response 54: EXPIRED CARD")


def test_an_empty_history_answers_nothing_rather_than_guessing():
    empty = ResolvedHistory()
    assert empty.exact("anything") is None
    assert empty.neighbours("anything") == []
    distribution, level = empty.prior(
        surface=Surface.PAYMENT, rail="card", step="x", source="y", code="z"
    )
    assert distribution == [] and level == "global"


def test_one_exemplar_is_not_enough_to_move_a_prior():
    """min_support guards against a single resolved episode becoming a law."""
    history = ResolvedHistory()
    history.add(Exemplar("odd wording", "C", "issuer", "authorization",
                         Surface.PAYMENT, "card", RootCause.RISK_DECLINED))
    _, level = history.prior(surface=Surface.PAYMENT, rail="card",
                             step="authorization", source="issuer", code="C")
    assert level == "global", "a single episode must not become a conditional prior"
