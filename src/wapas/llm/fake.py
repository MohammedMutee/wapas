"""Deterministic provider used by the test suite and by offline replay.

The entire test suite runs against this: no network, no credits, no flakes, and
identical output on every run. It is also what ``wapas replay`` uses to
re-derive a historical decision — responses are looked up by ``prompt_hash``,
so a replay reproduces exactly what the live model said at the time.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .base import LLMProvider, LLMResponse, ModelProfile, StructuredMode, Usage, prompt_digest


class FakeProvider(LLMProvider):
    """Replays canned responses, or generates them from a rule."""

    def __init__(
        self,
        *,
        responses: dict[str, str] | None = None,
        rule: Callable[[str, str], Any] | None = None,
        default: Any = None,
        supports: tuple[StructuredMode, ...] = (
            StructuredMode.JSON_SCHEMA,
            StructuredMode.JSON_OBJECT,
            StructuredMode.PROMPTED,
        ),
    ) -> None:
        self.name = "fake"
        self._by_hash = responses or {}
        self._rule = rule
        self._default = default
        self._supports = supports
        self.calls: list[tuple[str, str, StructuredMode]] = []

    def profile(self, model: str) -> ModelProfile:
        return ModelProfile(name=model, supports=self._supports, timeout_s=1.0)

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
        self.calls.append((system, user, mode))
        digest = prompt_digest(system, user, model, mode)

        if digest in self._by_hash:
            content = self._by_hash[digest]
        elif self._rule is not None:
            content = json.dumps(self._rule(system, user))
        elif self._default is not None:
            content = self._default if isinstance(self._default, str) else json.dumps(self._default)
        else:
            raise KeyError(f"FakeProvider has no response for prompt {digest[:12]}…")

        return LLMResponse(
            content=content,
            model=model,
            usage=Usage(input_tokens=len(system + user) // 4, output_tokens=len(content) // 4),
            latency_ms=1,
            mode=mode,
            prompt_hash=digest,
        )
