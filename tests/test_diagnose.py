"""Tests for the diagnosis step.

None of these touch the network. The contract being tested is not "the model is
clever" — that is what the evaluation measures — but "nothing unvalidated,
unpriced, or private ever leaves or enters this module."
"""

from __future__ import annotations

import datetime as _dt
import json

import pytest

from wapas.clock import IST
from wapas.diagnose import SYSTEM, DiagnosisCache, DiagnosisResponse, LLMDiagnoser
from wapas.diagnose.prompt import build_user_prompt
from wapas.domain import RootCause, Surface
from wapas.llm import FakeProvider
from wapas.llm.base import ProviderError, StructuredMode
from wapas.llm.costs import CostBook
from wapas.llm.retry import RetryingProvider
from wapas.money import Paise
from wapas.strategies import LLMAgent
from wapas.strategies.base import StrategyContext

NOW = _dt.datetime(2026, 6, 1, 12, 0, tzinfo=IST)
MODEL = "openai/gpt-oss-120b"


def ctx(**over) -> StrategyContext:
    base = dict(
        opened_at=NOW, now=NOW, surface=Surface.PAYMENT, amount_paise=Paise(250_000),
        rail="card", error_code="BAD_REQUEST_ERROR",
        error_description="Issuer response 51: NOT SUFFICIENT FUNDS",
        error_source="issuer", error_step="authorization", attempt_no=1,
        is_business=False,
    )
    base.update(over)
    return StrategyContext(**base)


def good(cause: str = "insufficient_funds", **over) -> dict:
    payload = {
        "signal_quality": "specific",
        "root_cause": cause, "confidence": 0.9, "evidence": ["response 51"],
        "recoverable": True, "recommended_horizon_hours": 72,
        "alternative_cause": None, "notes": "",
    }
    payload.update(over)
    return payload


def make(provider, **over) -> LLMDiagnoser:
    kwargs = dict(model=MODEL, costs=CostBook.load("config/rates.yaml"), cache=None)
    kwargs.update(over)
    return LLMDiagnoser(provider, **kwargs)


# ── the prompt ───────────────────────────────────────────────────────────────


def test_the_prompt_carries_no_personal_data():
    """The model has no need to know who the customer is, so it is not told."""
    prompt = build_user_prompt(
        surface=Surface.PAYMENT, rail="upi", error_code="BAD_REQUEST_ERROR",
        error_description="Payment failed", error_source="issuer",
        error_step="authorization", amount_paise=Paise(1_234_567), is_business=False,
    )
    lowered = prompt.lower()
    for forbidden in ("@", "+91", "phone", "email", "name", "vpa", "customer_id"):
        assert forbidden not in lowered, f"{forbidden!r} leaked into the diagnosis prompt"


def test_amounts_are_banded_so_the_cache_can_work():
    """Two episodes differing only in exact amount must share a prompt."""
    def prompt(paise: int) -> str:
        return build_user_prompt(
            surface=Surface.PAYMENT, rail="card", error_code="X", error_description="y",
            error_source="issuer", error_step="authorization",
            amount_paise=Paise(paise), is_business=False,
        )
    assert prompt(250_000) == prompt(260_000)
    assert prompt(250_000) != prompt(9_000_000)
    assert "2,50,000" not in prompt(250_000)


def test_the_system_prompt_lists_the_whole_taxonomy():
    for cause in RootCause:
        assert cause.value in SYSTEM, f"{cause} is missing from the taxonomy the model sees"


# ── validation ───────────────────────────────────────────────────────────────


def test_a_hallucinated_cause_never_reaches_the_planner():
    """An invented cause must fail validation and degrade, not pass through."""
    provider = FakeProvider(default=good(cause="bank_was_grumpy"),
                            supports=(StructuredMode.PROMPTED,))
    d = make(provider)
    result = d.diagnose(ctx())
    assert result.root_cause in set(RootCause)
    assert "degraded to rules" in result.notes
    assert d.stats.failures == 1


def test_unparseable_output_degrades_to_rules():
    provider = FakeProvider(default="I think the card had no money in it.",
                            supports=(StructuredMode.PROMPTED,))
    d = make(provider)
    result = d.diagnose(ctx())
    assert result.root_cause is RootCause.INSUFFICIENT_FUNDS  # the keyword rule found it
    assert "degraded to rules" in result.notes


def test_a_provider_outage_degrades_rather_than_failing_the_run():
    class Dead:
        name = "dead"

        def profile(self, model):
            return FakeProvider().profile(model)

        def complete(self, **_):
            raise ProviderError("connection reset", retryable=True)

    d = make(Dead())
    result = d.diagnose(ctx())
    assert result.root_cause is RootCause.INSUFFICIENT_FUNDS
    assert result.confidence <= 0.6, "a degraded answer must not claim full confidence"


def test_a_valid_response_is_used_as_given():
    provider = FakeProvider(default=good(cause="issuer_down", confidence=0.71),
                            supports=(StructuredMode.PROMPTED,))
    result = make(provider).diagnose(ctx())
    assert result.root_cause is RootCause.ISSUER_DOWN
    assert result.confidence == pytest.approx(0.71)


def test_the_runner_up_is_recorded_when_the_signal_is_ambiguous():
    provider = FakeProvider(
        default=good(cause="risk_declined", alternative_cause="insufficient_funds"),
        supports=(StructuredMode.PROMPTED,),
    )
    result = make(provider).diagnose(ctx())
    assert any("insufficient_funds" in e for e in result.evidence)


# ── cost ─────────────────────────────────────────────────────────────────────


def test_every_call_is_priced_and_drained_once():
    provider = FakeProvider(default=good(), supports=(StructuredMode.PROMPTED,))
    d = make(provider)
    d.diagnose(ctx())
    first = d.drain_cost()
    assert first > 0, "a model call that costs nothing would flatter the net figure"
    assert d.drain_cost() == 0, "cost must not be bookable twice"


def test_the_budget_is_a_ceiling_not_a_warning():
    provider = FakeProvider(default=good(), supports=(StructuredMode.PROMPTED,))
    d = make(provider, budget_usd=0.0)
    result = d.diagnose(ctx())
    assert d.stats.budget_stops == 1
    assert d.stats.calls == 0
    assert "budget" in result.notes


# ── cache ────────────────────────────────────────────────────────────────────


def test_the_cache_makes_a_repeated_question_free(tmp_path):
    provider = FakeProvider(default=good(), supports=(StructuredMode.PROMPTED,))
    cache = DiagnosisCache(path=tmp_path / "d.json")
    d = make(provider, cache=cache)

    d.diagnose(ctx())
    d.diagnose(ctx())
    assert d.stats.calls == 1
    assert d.stats.cache_hits == 1
    assert d.drain_cost() == 0, "a cache hit costs nothing and must not be billed"


def test_the_cache_survives_a_round_trip_and_a_corrupt_file(tmp_path):
    path = tmp_path / "d.json"
    cache = DiagnosisCache(path=path)
    cache.put("abc", good())
    cache.save()
    assert DiagnosisCache(path=path).get("abc") == good()

    path.write_text("{ not json", encoding="utf-8")
    assert DiagnosisCache(path=path).entries == {}, "a corrupt cache must not be fatal"


def test_changing_the_prompt_invalidates_the_cache(tmp_path):
    provider = FakeProvider(default=good(), supports=(StructuredMode.PROMPTED,))
    cache = DiagnosisCache(path=tmp_path / "d.json")
    d = make(provider, cache=cache)
    d.diagnose(ctx())
    d.diagnose(ctx(error_description="Transaction declined"))
    assert d.stats.calls == 2, "a different question must not be answered from cache"


# ── retry wrapper ────────────────────────────────────────────────────────────


class Flaky:
    name = "flaky"

    def __init__(self, fail_times: int, retryable: bool = True) -> None:
        self.fail_times = fail_times
        self.retryable = retryable
        self.calls = 0

    def profile(self, model):
        return FakeProvider().profile(model)

    def complete(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ProviderError("timeout", retryable=self.retryable)
        return FakeProvider(default=good()).complete(**kwargs)


def test_retries_recover_from_a_flaky_endpoint():
    inner = Flaky(fail_times=2)
    slept: list[float] = []
    provider = RetryingProvider(inner, attempts=3, base_delay_s=1.0, sleep=slept.append)
    response = provider.complete(model=MODEL, system="s", user="u",
                                 mode=StructuredMode.PROMPTED)
    assert json.loads(response.content)["root_cause"] == "insufficient_funds"
    assert slept == [1.0, 2.0], "backoff must be exponential"
    assert provider.retries_used == 2


def test_a_non_retryable_error_is_not_retried():
    """A 401 is a bug. Retrying it three times just delays finding out."""
    inner = Flaky(fail_times=1, retryable=False)
    provider = RetryingProvider(inner, attempts=3, sleep=lambda _: None)
    with pytest.raises(ProviderError):
        provider.complete(model=MODEL, system="s", user="u", mode=StructuredMode.PROMPTED)
    assert inner.calls == 1


def test_retries_are_bounded():
    inner = Flaky(fail_times=99)
    provider = RetryingProvider(inner, attempts=3, sleep=lambda _: None)
    with pytest.raises(ProviderError):
        provider.complete(model=MODEL, system="s", user="u", mode=StructuredMode.PROMPTED)
    assert inner.calls == 3


# ── the agent ────────────────────────────────────────────────────────────────


def test_the_agent_plans_from_the_same_playbooks_as_the_rules_baseline():
    """The ablation is only clean if the *only* difference is the diagnosis."""
    from wapas.strategies import RulesOnly

    provider = FakeProvider(default=good(cause="issuer_down"),
                            supports=(StructuredMode.PROMPTED,))
    agent = LLMAgent(make(provider))
    rules = RulesOnly()

    situation = ctx(error_description="The issuing bank is not reachable at the moment")
    assert rules.diagnose(situation).root_cause is RootCause.ISSUER_DOWN

    for step in range(3):
        at = ctx(error_description=situation.error_description, step_no=step,
                 diagnosis=agent.diagnose(situation))
        by_rules = rules.next_action(
            ctx(error_description=situation.error_description, step_no=step)
        )
        by_agent = agent.next_action(at)
        assert (by_agent is None) == (by_rules is None)
        if by_agent is not None:
            assert by_agent.tool is by_rules.tool


def test_the_agent_reports_its_token_cost_to_the_episode():
    provider = FakeProvider(default=good(), supports=(StructuredMode.PROMPTED,))
    agent = LLMAgent(make(provider))
    agent.diagnose(ctx())
    assert agent.drain_cost() > 0
    assert agent.drain_cost() == 0


def test_the_response_schema_rejects_out_of_range_confidence():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DiagnosisResponse.model_validate(good(confidence=1.4))
    with pytest.raises(ValidationError):
        DiagnosisResponse.model_validate(good(extra_field="surprise"))


def test_a_generic_signal_cannot_support_a_confident_answer():
    """The check that exists because prose alone did not work.

    Told plainly not to, the live endpoint still answered a bare "Transaction
    declined" with `gateway_error` at 0.95. Grading the signal is a separate
    field, so the contradiction between "this text names no mechanism" and
    "I am 95% sure which mechanism it was" is machine-checkable.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="caps confidence"):
        DiagnosisResponse.model_validate(
            good(signal_quality="generic", confidence=0.95)
        )
    with pytest.raises(ValidationError, match="caps confidence"):
        DiagnosisResponse.model_validate(good(signal_quality="weak", confidence=0.9))

    # Within the cap it is accepted unchanged.
    ok = DiagnosisResponse.model_validate(good(signal_quality="generic", confidence=0.4))
    assert ok.confidence == pytest.approx(0.4)


def test_an_overconfident_answer_is_sent_back_before_it_is_accepted():
    """The retry ladder must fix the contradiction, not degrade on it."""
    replies = iter([
        good(signal_quality="generic", confidence=0.95, cause="gateway_error"),
        good(signal_quality="generic", confidence=0.35, cause="unknown"),
    ])
    provider = FakeProvider(rule=lambda system, user: next(replies),
                            supports=(StructuredMode.PROMPTED,))
    result = make(provider).diagnose(ctx(error_description="Transaction declined"))
    assert result.root_cause is RootCause.UNKNOWN
    assert "degraded to rules" not in result.notes
