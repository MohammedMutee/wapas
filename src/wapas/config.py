"""Runtime configuration.

Loaded from the environment (and ``.env``), validated once at startup. Two
guards live here rather than in documentation, because a guard that is only
written down is not a guard:

* **No live Razorpay keys.** A key beginning ``rzp_live_`` raises at load. There
  is no code path in this repository that touches live mode, and this makes
  that structural rather than aspirational.
* **A hard LLM spend ceiling.** An evaluation run that would exceed
  ``WAPAS_LLM_BUDGET_USD`` aborts rather than quietly burning credit.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Mode(StrEnum):
    SIM = "sim"
    """Virtual clock, simulated channels, no external side effects."""
    LIVE = "live"
    """Real clock, Razorpay test-mode API, real channel adapters."""


class Provider(StrEnum):
    NVIDIA = "nvidia"
    ANTHROPIC = "anthropic"
    FAKE = "fake"
    """Deterministic stub. The whole test suite runs on this — no network."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="", extra="ignore", case_sensitive=False
    )

    mode: Mode = Field(default=Mode.SIM, alias="WAPAS_MODE")
    seed: int = Field(default=20260901, alias="WAPAS_SEED")

    database_url: str = "postgresql+psycopg://wapas:wapas@localhost:5433/wapas"
    redis_url: str = "redis://localhost:6380/0"

    # ── LLM ──────────────────────────────────────────────────────────────────
    llm_provider: Provider = Field(default=Provider.FAKE, alias="WAPAS_LLM_PROVIDER")
    nvidia_api_key: SecretStr | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    anthropic_api_key: SecretStr | None = None
    model_reasoning: str = Field(default="", alias="WAPAS_MODEL_REASONING")
    model_cheap: str = Field(default="", alias="WAPAS_MODEL_CHEAP")
    llm_budget_usd: float = Field(default=15.0, alias="WAPAS_LLM_BUDGET_USD", ge=0)

    # ── Razorpay (test mode only) ────────────────────────────────────────────
    razorpay_key_id: str = ""
    razorpay_key_secret: SecretStr | None = None
    razorpay_webhook_secret: SecretStr | None = None

    # ── channels ─────────────────────────────────────────────────────────────
    whatsapp_token: SecretStr | None = None
    whatsapp_phone_id: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: SecretStr | None = None
    resend_api_key: SecretStr | None = None

    @model_validator(mode="after")
    def _test_mode_only(self) -> Self:
        if self.razorpay_key_id.startswith("rzp_live_"):
            raise ValueError(
                "A live Razorpay key was supplied. Wapas is test-mode only and has no "
                "live code path; refusing to start."
            )
        if self.razorpay_key_id and not self.razorpay_key_id.startswith("rzp_test_"):
            raise ValueError(f"unrecognised Razorpay key prefix: {self.razorpay_key_id[:9]}…")
        return self

    @model_validator(mode="after")
    def _provider_has_credentials(self) -> Self:
        if self.llm_provider is Provider.NVIDIA and not self.nvidia_api_key:
            raise ValueError("WAPAS_LLM_PROVIDER=nvidia but NVIDIA_API_KEY is unset")
        if self.llm_provider is Provider.ANTHROPIC and not self.anthropic_api_key:
            raise ValueError("WAPAS_LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is unset")
        return self

    @property
    def has_razorpay(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Process-wide settings. Cached so validation runs exactly once."""
    return Settings()
