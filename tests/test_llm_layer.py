"""Tests for the provider-neutral LLM layer.

All of these run against :class:`FakeProvider` — no network, no credits, no
flakes. The point of the layer is that the rest of Wapas cannot tell which
model answered, and these tests hold that line.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, Field

from wapas.llm import FakeProvider, StructuredCallError, StructuredMode, Usage, ask_structured
from wapas.llm.base import ProviderError, prompt_digest
from wapas.llm.costs import CostBook, cost_paise
from wapas.llm.structured import extract_json


class Diag(BaseModel):
    root_cause: str = Field(pattern="^(insufficient_funds|risk_declined)$")
    confidence: float = Field(ge=0, le=1)


VALID = {"root_cause": "insufficient_funds", "confidence": 0.82}


# ── the extraction ladder ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Here is my answer:\n{"a": 1}\nHope that helps!',
        'Reasoning: the error says X, so...\n\n{"a": 1}',
    ],
)
def test_json_is_extracted_from_chatty_replies(raw):
    """Open models add prose even when told not to. That is what this is for."""
    assert json.loads(extract_json(raw)) == {"a": 1}


# ── validation and retry ─────────────────────────────────────────────────────


def test_valid_response_passes_first_time():
    provider = FakeProvider(default=VALID)
    parsed, response = ask_structured(
        provider, model="m", system="s", user="u", schema_model=Diag
    )
    assert parsed.root_cause == "insufficient_funds"
    assert response.attempts == 1


def test_invalid_response_is_retried_with_the_error_fed_back():
    """Models are much better at fixing a named error than at avoiding it."""
    replies = iter([{"root_cause": "not_a_cause", "confidence": 2.0}, VALID])
    provider = FakeProvider(rule=lambda s, u: next(replies))
    parsed, response = ask_structured(
        provider, model="m", system="s", user="u", schema_model=Diag
    )
    assert parsed.root_cause == "insufficient_funds"
    assert response.attempts == 2
    # The second prompt must carry the rejection so the model can correct itself.
    assert "was rejected" in provider.calls[1][1]


def test_exhausted_retries_raise_rather_than_return_junk():
    """Nothing downstream ever sees unvalidated model output. It degrades instead."""
    provider = FakeProvider(default={"root_cause": "nonsense", "confidence": 9})
    with pytest.raises(StructuredCallError) as exc:
        ask_structured(provider, model="m", system="s", user="u", schema_model=Diag)
    assert exc.value.attempts, "the failure must record what was tried"


def test_ladder_descends_when_a_mode_is_unsupported():
    """A model with only PROMPTED support must never be asked for json_schema."""
    provider = FakeProvider(default=VALID, supports=(StructuredMode.PROMPTED,))
    _parsed, response = ask_structured(
        provider, model="m", system="s", user="u", schema_model=Diag
    )
    assert response.mode is StructuredMode.PROMPTED
    # In PROMPTED mode the schema is inlined into the prompt.
    assert "JSON Schema" in provider.calls[0][1]


def test_non_retryable_provider_error_drops_a_rung_immediately():
    class Failing(FakeProvider):
        def complete(self, **kw):
            if kw["mode"] is not StructuredMode.PROMPTED:
                raise ProviderError("400 bad request", retryable=False)
            return super().complete(**kw)

    provider = Failing(default=VALID)
    _parsed, response = ask_structured(
        provider, model="m", system="s", user="u", schema_model=Diag
    )
    assert response.mode is StructuredMode.PROMPTED


# ── determinism ──────────────────────────────────────────────────────────────


def test_prompt_digest_is_stable_and_mode_sensitive():
    a = prompt_digest("s", "u", "m", StructuredMode.JSON_OBJECT)
    assert a == prompt_digest("s", "u", "m", StructuredMode.JSON_OBJECT)
    assert a != prompt_digest("s", "u", "m", StructuredMode.PROMPTED)
    assert a != prompt_digest("s", "u2", "m", StructuredMode.JSON_OBJECT)


def test_replay_by_prompt_hash():
    """How `wapas replay` re-derives a historical decision without a network call."""
    # Mirror a real gpt-oss profile so the first rung attempted is JSON_OBJECT,
    # which is the mode the historical response was recorded under.
    digest = prompt_digest("sys", "usr", "m", StructuredMode.JSON_OBJECT)
    provider = FakeProvider(
        responses={digest: json.dumps(VALID)},
        supports=(StructuredMode.JSON_OBJECT, StructuredMode.PROMPTED),
    )
    parsed, _ = ask_structured(provider, model="m", system="sys", user="usr", schema_model=Diag)
    assert parsed.confidence == 0.82


# ── cost accounting ──────────────────────────────────────────────────────────


def test_cost_is_charged_in_whole_paise_rounded_up():
    """When the cost line is uncertain, understate the net recovery, never overstate."""
    book = CostBook.load("config/rates.yaml")
    tiny = cost_paise(Usage(input_tokens=1, output_tokens=1), "openai/gpt-oss-120b", book)
    assert tiny == 1, "a near-zero cost must still round up to a paisa, not to zero"


def test_cached_tokens_are_discounted():
    book = CostBook.load("config/rates.yaml")
    plain = cost_paise(Usage(input_tokens=100_000), "claude-opus-5", book)
    cached = cost_paise(
        Usage(input_tokens=100_000, cached_input_tokens=90_000), "claude-opus-5", book
    )
    assert cached < plain


def test_batch_tier_halves_the_cost():
    book = CostBook.load("config/rates.yaml")
    u = Usage(input_tokens=1_000_000, output_tokens=100_000)
    assert cost_paise(u, "claude-opus-5", book, batch=True) < cost_paise(u, "claude-opus-5", book)


def test_an_unpriced_model_is_an_error_not_a_free_lunch():
    """An unpriced model would silently zero out the cost line."""
    book = CostBook.load("config/rates.yaml")
    with pytest.raises(KeyError, match="no rate for model"):
        cost_paise(Usage(input_tokens=10), "some/unpriced-model", book)


def test_notional_rates_are_declared():
    """Free-tier models are priced notionally and the report must say so."""
    book = CostBook.load("config/rates.yaml")
    assert "openai/gpt-oss-120b" in book.any_notional()
    assert "claude-opus-5" not in book.any_notional()
