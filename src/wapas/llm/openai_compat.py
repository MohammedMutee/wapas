"""OpenAI-compatible provider — used for NVIDIA NIM.

NVIDIA serves open models at ``https://integrate.api.nvidia.com/v1`` behind the
OpenAI chat-completions shape, authenticated with an ``nvapi-`` bearer key.

The model registry below records *measured* capability, not documented
capability. Every entry was probed with an identical diagnosis prompt; where a
mode hangs or produces invalid output it is simply absent from ``supports``, so
the structured-call ladder never attempts it.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .base import (
    LLMProvider,
    LLMResponse,
    ModelProfile,
    ProviderError,
    StructuredMode,
    Usage,
    prompt_digest,
)

# Measured on the NVIDIA catalogue, 2026-08-22, with the diagnosis probe in
# scripts/probe_models.py. Re-run that script before trusting these.
NVIDIA_PROFILES: dict[str, ModelProfile] = {
    # ── recommended ──────────────────────────────────────────────────────────
    "openai/gpt-oss-120b": ModelProfile(
        name="openai/gpt-oss-120b",
        supports=(StructuredMode.JSON_OBJECT, StructuredMode.PROMPTED),
        timeout_s=90.0,
        measured_on="2026-08-22",
        notes="DEFAULT REASONING MODEL. Correct on the probe in 3.4s / 582 tok. "
              "Strict json_schema hangs, so it is deliberately not offered.",
    ),
    # ── probed, not selected ─────────────────────────────────────────────────
    "nvidia/nemotron-3-super-120b-a12b": ModelProfile(
        name="nvidia/nemotron-3-super-120b-a12b",
        supports=(StructuredMode.JSON_SCHEMA, StructuredMode.JSON_OBJECT, StructuredMode.PROMPTED),
        timeout_s=90.0,
        measured_on="2026-08-22",
        notes="Only model that honours strict json_schema, and fast (4.9s) — but it was "
              "WRONG on the probe: answered card_expired_or_invalid for an error whose "
              "description literally reads 'insufficient balance'. Schema compliance is "
              "not accuracy, which is the whole reason model choice is evaluated and not "
              "assumed.",
    ),
    "nvidia/nemotron-3-ultra-550b-a55b": ModelProfile(
        name="nvidia/nemotron-3-ultra-550b-a55b",
        supports=(StructuredMode.JSON_OBJECT, StructuredMode.PROMPTED),
        timeout_s=120.0,
        measured_on="2026-08-22",
        notes="Correct answer but 54.7s and did not fill the full schema. At 2,000 "
              "episodes that latency is disqualifying.",
    ),
    "nvidia/nemotron-3.5-lightning-30b-a3b": ModelProfile(
        name="nvidia/nemotron-3.5-lightning-30b-a3b",
        supports=(StructuredMode.PROMPTED,),
        timeout_s=60.0,
        measured_on="2026-08-22",
        notes="Emits reasoning prose around the JSON; needs extraction. Candidate for "
              "the cheap classification tier once the extractor is exercised on it.",
    ),
    "nvidia/nvidia-nemotron-nano-9b-v2": ModelProfile(
        name="nvidia/nvidia-nemotron-nano-9b-v2",
        supports=(StructuredMode.PROMPTED,),
        timeout_s=60.0,
        measured_on="2026-08-22",
        notes="Same reasoning-prose behaviour as lightning-30b, 16s. Cheap tier candidate.",
    ),
    "meta/llama-3.3-70b-instruct": ModelProfile(
        name="meta/llama-3.3-70b-instruct",
        supports=(StructuredMode.PROMPTED,),
        timeout_s=120.0,
        measured_on="2026-08-22",
        notes="Timed out in both structured modes (55s and 110s). Deprioritised.",
    ),
    "moonshotai/kimi-k3": ModelProfile(
        name="moonshotai/kimi-k3",
        supports=(StructuredMode.PROMPTED,),
        timeout_s=120.0,
        measured_on="2026-08-22",
        notes="Timed out at 110s.",
    ),
}

UNSERVED: frozenset[str] = frozenset({
    "moonshotai/kimi-k2.6",
    "mistralai/mistral-large-2-instruct",
})
"""Models listed by ``GET /v1/models`` that return 404 on inference.

The catalogue endpoint is not a reliable statement of what is actually
callable, so anything here is skipped rather than retried.
"""

DEFAULT_PROFILE = ModelProfile(
    name="unknown",
    supports=(StructuredMode.JSON_OBJECT, StructuredMode.PROMPTED),
    timeout_s=90.0,
    notes="Unprobed model: assume JSON mode, fall back to prompted extraction.",
)


class OpenAICompatProvider(LLMProvider):
    """Chat-completions client for any OpenAI-shaped endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://integrate.api.nvidia.com/v1",
        name: str = "nvidia",
        profiles: dict[str, ModelProfile] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self._profiles = profiles if profiles is not None else NVIDIA_PROFILES
        self._client = client or httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    def profile(self, model: str) -> ModelProfile:
        return self._profiles.get(model, ModelProfile(**{**DEFAULT_PROFILE.__dict__, "name": model}))

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
        prof = self.profile(model)
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            **prof.extra_body,
        }

        if mode is StructuredMode.JSON_SCHEMA:
            if schema is None:
                raise ValueError("JSON_SCHEMA mode requires a schema")
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema, "strict": True},
            }
        elif mode is StructuredMode.JSON_OBJECT:
            body["response_format"] = {"type": "json_object"}

        started = time.monotonic()
        try:
            resp = self._client.post(
                f"{self.base_url}/chat/completions", json=body, timeout=prof.timeout_s
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{model}: timed out after {prof.timeout_s}s", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{model}: transport error: {exc}", retryable=True) from exc

        latency_ms = int((time.monotonic() - started) * 1000)

        if resp.status_code == 429:
            raise ProviderError(f"{model}: rate limited", retryable=True)
        if resp.status_code >= 500:
            raise ProviderError(f"{model}: upstream {resp.status_code}", retryable=True)
        if resp.status_code >= 400:
            raise ProviderError(f"{model}: {resp.status_code} {resp.text[:200]}", retryable=False)

        try:
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
        except (json.JSONDecodeError, KeyError, IndexError) as exc:
            raise ProviderError(f"{model}: malformed response envelope: {exc}") from exc

        u = data.get("usage") or {}
        return LLMResponse(
            content=content,
            model=model,
            usage=Usage(
                input_tokens=int(u.get("prompt_tokens", 0)),
                output_tokens=int(u.get("completion_tokens", 0)),
                cached_input_tokens=int(
                    (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
                ),
            ),
            latency_ms=latency_ms,
            mode=mode,
            prompt_hash=prompt_digest(system, user, model, mode),
            finish_reason=str(choice.get("finish_reason", "")),
        )

    def close(self) -> None:
        self._client.close()
