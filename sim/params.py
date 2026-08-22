"""Typed access to ``sim/params.yaml``.

Validated on load: cause shares must sum to one, distributions must be
recognised, volumes must be positive. A malformed world model should fail
immediately, not produce quietly wrong numbers.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Dist(_B):
    """A named distribution, drawn through :class:`~sim.rng.Rng`."""

    dist: str
    a: float | None = None
    b: float | None = None
    lam: float | None = None
    mu: float | None = None
    sigma: float | None = None

    @model_validator(mode="after")
    def _known(self) -> Self:
        if self.dist not in {"beta", "poisson", "lognormal", "uniform"}:
            raise ValueError(f"unknown distribution {self.dist!r}")
        return self


class Categorical(_B):
    values: tuple[Any, ...]
    weights: tuple[float, ...]

    @model_validator(mode="after")
    def _aligned(self) -> Self:
        if len(self.values) != len(self.weights):
            raise ValueError("values and weights must be the same length")
        if not math.isclose(sum(self.weights), 1.0, abs_tol=1e-6):
            raise ValueError(f"weights must sum to 1.0, got {sum(self.weights)}")
        return self


class Amounts(_B):
    distribution: str
    mu: float
    sigma: float
    min_rupees: int
    max_rupees: int


class Outages(_B):
    bursts_per_90_days: int
    burst_duration_minutes: dict[str, int]
    affected_issuer_share: float


class ConsumerParams(_B):
    liquidity_refresh_day: Categorical
    responsiveness: Dist
    annoyance_threshold: Dist
    price_sensitivity: Dist
    channel_preference: Categorical
    self_recovery_rate: Dist


class B2BParams(_B):
    persona: Categorical
    promise_keep_rate: Dist
    dispute_propensity: Dist
    days_late_baseline: Dist
    self_recovery_rate: Dist


class Timing(_B):
    liquidity_bonus: float
    liquidity_penalty: float
    liquidity_window_days: int
    issuer_recovered_bonus: float
    issuer_still_down_penalty: float
    business_hours_bonus: float


class Response(_B):
    base_log_odds: float
    intervention_lift: dict[str, float]
    cause_fit: dict[str, dict[str, float]]
    timing: Timing
    channel_fit_bonus: float
    fatigue_lambda: float
    opt_out_hazard_per_contact: float
    complaint_hazard_per_contact: float
    concession_elasticity: float
    part_payment_probability: float
    part_payment_fraction: dict[str, float]
    promise_to_pay_probability: float
    promise_horizon_days: dict[str, int]


class Volumes(_B):
    payment_episodes: int = Field(ge=0)
    mandate_episodes: int = Field(ge=0)
    receivable_episodes: int = Field(ge=0)

    @property
    def total(self) -> int:
        return self.payment_episodes + self.mandate_episodes + self.receivable_episodes


class SimParams(_B):
    version: str
    failure_causes: dict[str, float]
    rails: dict[str, dict[str, float]]
    amounts: Amounts
    issuer_outages: Outages
    consumer: ConsumerParams
    b2b_buyer: B2BParams
    response: Response
    volumes: Volumes
    horizon_days: int = Field(ge=1)

    @model_validator(mode="after")
    def _shares_sum_to_one(self) -> Self:
        total = sum(self.failure_causes.values())
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(f"failure_causes must sum to 1.0, got {total}")
        return self

    def perturbed(self, factor: float, keys: tuple[str, ...]) -> SimParams:
        """Return a copy with named scalar parameters scaled.

        Used by the sensitivity sweep to show the agent's advantage is not an
        artefact of the exact parameter values.
        """
        data = self.model_dump()

        def scale(node: Any, path: str) -> Any:
            if isinstance(node, dict):
                return {k: scale(v, f"{path}.{k}" if path else k) for k, v in node.items()}
            if isinstance(node, (int, float)) and not isinstance(node, bool):
                if any(path.endswith(k) or f".{k}." in f".{path}." for k in keys):
                    return node * factor
            return node

        return SimParams.model_validate(scale(data, ""))


def load_params(path: str | Path = "sim/params.yaml") -> SimParams:
    return SimParams.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
