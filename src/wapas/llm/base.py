"""Provider-neutral request/response types."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class StructuredMode(StrEnum):
    """How a model is asked to produce machine-readable output.

    Ordered from strongest guarantee to weakest. :func:`wapas.llm.structured.
    ask_structured` walks down this ladder when a model does not support the
    rung above.
    """

    JSON_SCHEMA = "json_schema"
    """Provider constrains decoding to the schema. Strongest, least supported."""
    JSON_OBJECT = "json_object"
    """Provider guarantees syntactically valid JSON; the shape is on us."""
    PROMPTED = "prompted"
    """Nothing is guaranteed; JSON is extracted from the text and validated."""


@dataclass(frozen=True, slots=True)
class Usage:
    """Token accounting for one call. Persisted per decision.

    This is what makes the "net of LLM cost" headline metric real rather than
    estimated — see :mod:`wapas.llm.costs`.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cached_input_tokens + other.cached_input_tokens,
        )


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """One model reply, with everything the audit log needs to reconstruct it."""

    content: str
    model: str
    usage: Usage
    latency_ms: int
    mode: StructuredMode
    prompt_hash: str
    """Digest of the exact prompt. Replay looks up cached responses by this."""
    attempts: int = 1
    """How many tries the structured call needed. >1 means the model misbehaved."""
    finish_reason: str = ""


def prompt_digest(system: str, user: str, model: str, mode: StructuredMode) -> str:
    """Stable identifier for a prompt, used for caching and deterministic replay."""
    material = json.dumps(
        {"system": system, "user": user, "model": model, "mode": str(mode)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


@dataclass
class ModelProfile:
    """What a given model can actually do, as measured rather than advertised."""

    name: str
    supports: tuple[StructuredMode, ...]
    timeout_s: float = 60.0
    notes: str = ""
    measured_on: str = ""
    extra_body: dict[str, Any] = field(default_factory=dict)
    min_output_tokens: int = 0
    """Floor on ``max_tokens`` for this model, raised by the provider.

    Reasoning models spend the output budget thinking before they answer, and
    the budget is shared. Starve one and it does not answer briefly — it
    returns an empty completion with ``finish_reason=length`` after a long
    wait, which looks exactly like the endpoint being down. Measured on
    ``gpt-oss-120b`` on 2026-08-23: 60 tokens gave 74.8s and nothing, 900
    tokens gave 2.8s and a correct answer. The floor belongs to the model, so
    it lives in the model's profile rather than in every call site."""

    def best_mode(self) -> StructuredMode:
        return self.supports[0]


class LLMProvider(Protocol):
    """The one interface the rest of Wapas depends on."""

    name: str

    def profile(self, model: str) -> ModelProfile:
        """Capabilities of a model, for the structured-output ladder."""
        ...

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        mode: StructuredMode,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Single completion. Raises :class:`ProviderError` on transport failure."""
        ...


class ProviderError(RuntimeError):
    """Transport, auth, rate-limit or timeout failure talking to a provider."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
