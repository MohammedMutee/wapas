"""Structured output with graceful degradation.

The probe results in :mod:`wapas.llm` show why this exists: on an open-model
catalogue, strict schema decoding is available on some models, hangs on others,
and correctness is uncorrelated with either. So we do not depend on any single
mechanism. Instead:

1. Try the strongest structured mode the model actually supports.
2. Parse and validate the result against the Pydantic model.
3. On failure, retry with the validation error appended to the prompt — models
   are markedly better at *fixing* a named error than at avoiding it.
4. After exhausting retries at one rung, drop to the next rung of the ladder.
5. If everything fails, raise. **The caller degrades to the conservative
   playbook; it never proceeds on unvalidated model output.**

The last point is the safety-relevant one. Nothing downstream ever sees a value
that did not survive validation, and the policy gate independently re-checks
whatever the model proposed regardless.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

from .base import LLMProvider, LLMResponse, ProviderError, StructuredMode

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class StructuredCallError(RuntimeError):
    """Every rung of the ladder failed. The caller must degrade, not guess."""

    def __init__(self, message: str, *, attempts: list[str]) -> None:
        super().__init__(message)
        self.attempts = attempts


def extract_json(text: str) -> str:
    """Pull a JSON object out of a possibly chatty response.

    Handles fenced blocks, leading prose, and trailing commentary — all things
    open models do even when told not to.
    """
    text = text.strip()
    if m := _FENCE.search(text):
        return m.group(1).strip()
    if m := _OBJECT.search(text):
        return m.group(0)
    return text


def ask_structured[T: BaseModel](
    provider: LLMProvider,
    *,
    model: str,
    system: str,
    user: str,
    schema_model: type[T],
    max_retries: int = 2,
    max_tokens: int = 1024,
) -> tuple[T, LLMResponse]:
    """Obtain a validated instance of ``schema_model`` from the model.

    Returns the parsed object and the final :class:`LLMResponse`, whose
    ``attempts`` field records how many tries it took. A rising attempt count
    across a run is a signal worth putting on the dashboard: it is the model
    telling you it is struggling with the prompt.
    """
    schema = schema_model.model_json_schema()
    ladder = provider.profile(model).supports
    failures: list[str] = []
    attempt_no = 0

    for mode in ladder:
        prompt = user if mode is not StructuredMode.PROMPTED else (
            f"{user}\n\nRespond with a single JSON object and nothing else. "
            f"It must validate against this JSON Schema:\n{json.dumps(schema, indent=2)}"
        )

        for retry in range(max_retries + 1):
            attempt_no += 1
            try:
                response = provider.complete(
                    model=model,
                    system=system,
                    user=prompt,
                    mode=mode,
                    schema=schema if mode is StructuredMode.JSON_SCHEMA else None,
                    max_tokens=max_tokens,
                )
            except ProviderError as exc:
                failures.append(f"{mode}/try{retry + 1}: {exc}")
                if not exc.retryable:
                    break          # a 4xx will not improve on retry; drop a rung
                continue

            try:
                parsed = schema_model.model_validate_json(extract_json(response.content))
            except (ValidationError, ValueError) as exc:
                failures.append(f"{mode}/try{retry + 1}: {type(exc).__name__}: {str(exc)[:180]}")
                # Feed the error back. Naming the mistake is far more effective
                # than repeating the original instruction.
                prompt = (
                    f"{prompt}\n\nYour previous reply was rejected:\n{str(exc)[:500]}\n"
                    f"Return only corrected JSON."
                )
                continue

            object.__setattr__(response, "attempts", attempt_no)
            return parsed, response

    raise StructuredCallError(
        f"{model}: no structured mode produced a valid {schema_model.__name__} "
        f"after {attempt_no} attempts",
        attempts=failures,
    )
