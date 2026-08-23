"""Retry a flaky provider, without retrying things that should not be retried.

The NVIDIA developer endpoint is free and, unsurprisingly, variable: the model
bake-off recorded outright hangs on models that had answered a minute earlier.
An evaluation that aborts because one call in two thousand timed out is not
measuring the agent, it is measuring the weather.

Two rules keep this from becoming a way to hide problems:

* **Only retryable failures are retried.** A 401 or a malformed request is a
  bug and must surface immediately. ``ProviderError`` already carries the
  distinction; this wrapper honours it rather than catching everything.
* **The retries are counted and surfaced.** ``attempts_used`` is reported, so
  "the endpoint was flaky today" is a number in the report and not an excuse
  offered afterwards.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .base import LLMProvider, LLMResponse, ModelProfile, ProviderError, StructuredMode


class RetryingProvider:
    """Wraps a provider with bounded exponential backoff on retryable errors."""

    def __init__(
        self,
        inner: LLMProvider,
        *,
        attempts: int = 3,
        base_delay_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        self.inner = inner
        self.name = f"retrying({inner.name})"
        self.attempts = attempts
        self.base_delay_s = base_delay_s
        self._sleep = sleep
        self.retries_used = 0
        """Total extra calls made across this provider's lifetime."""
        self.failures: list[str] = []

    def profile(self, model: str) -> ModelProfile:
        return self.inner.profile(model)

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
        last: ProviderError | None = None
        for attempt in range(self.attempts):
            try:
                return self.inner.complete(
                    model=model, system=system, user=user, mode=mode, schema=schema,
                    max_tokens=max_tokens, temperature=temperature,
                )
            except ProviderError as exc:
                if not exc.retryable:
                    raise
                last = exc
                self.failures.append(f"{model}/{mode}: {exc}")
                if attempt + 1 < self.attempts:
                    self.retries_used += 1
                    self._sleep(self.base_delay_s * (2**attempt))
        assert last is not None
        raise last
