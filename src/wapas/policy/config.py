"""Typed loading and validation of the policy YAML files.

Policy is data, but it is *validated* data. A malformed or dangerous policy
file must fail at load time, loudly, rather than at 2 a.m. inside an episode.

Two invariants are enforced here and cannot be configured away:

``require_valid_mandate_for_debit``
    Attempting to set this false raises. There is no legitimate configuration
    of this system in which a debit is presented without a live mandate.

``skip_rungs``
    Attempting to set this true raises. Jumping escalation rungs is the exact
    behaviour the "compliant escalation" requirement exists to prevent.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..domain import Channel, RootCause


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QuietWindow(_Base):
    """A local-time window during which a channel must not be used."""

    start: _dt.time
    end: _dt.time
    tz: str = "Asia/Kolkata"
    source: str = ""
    verified: bool = False
    """False means: not independently confirmed. Never cite it as regulation."""

    def contains(self, local_time: _dt.time) -> bool:
        """Handles windows that wrap past midnight (21:00 → 08:00)."""
        if self.start <= self.end:
            return self.start <= local_time < self.end
        return local_time >= self.start or local_time < self.end


class QuietHours(_Base):
    messaging: QuietWindow
    voice: QuietWindow

    def for_channel(self, channel: Channel) -> QuietWindow:
        return self.voice if channel is Channel.VOICE else self.messaging


class FrequencyCaps(_Base):
    messages_per_day: int = Field(ge=0)
    messages_per_week: int = Field(ge=0)
    voice_calls_per_week: int = Field(ge=0)
    contacts_per_episode: int = Field(ge=0)
    cooldown_after_no_response_hours: int = Field(ge=0)


class ConsentPolicy(_Base):
    require_channel_consent: bool
    honour_dnd_registry: bool
    opt_out_keywords: tuple[str, ...]
    opt_out_is_permanent: bool
    opt_out_propagates_across_surfaces: bool


class ThirdPartyPolicy(_Base):
    contact_non_debtor_parties: bool
    disclose_debt_to_third_party: bool

    @model_validator(mode="after")
    def _no_third_party_contact(self) -> Self:
        if self.contact_non_debtor_parties or self.disclose_debt_to_third_party:
            raise ValueError(
                "third-party contact and debt disclosure are invariants; they cannot be enabled"
            )
        return self


class ContentPolicy(_Base):
    require_identification: bool
    require_opt_out_line: bool
    banned_claims: tuple[str, ...]
    max_freetext_slot_chars: int = Field(ge=0)


class ContactPolicy(_Base):
    version: str
    quiet_hours: QuietHours
    frequency_caps: FrequencyCaps
    consent: ConsentPolicy
    third_parties: ThirdPartyPolicy
    content: ContentPolicy


class MoneyActionPolicy(_Base):
    require_valid_mandate_for_debit: bool
    max_retries_per_payment: int = Field(ge=0)
    min_gap_between_retries_hours: int = Field(ge=0)
    never_retry_causes: tuple[RootCause, ...]
    verify_before_retry_causes: tuple[RootCause, ...]

    @model_validator(mode="after")
    def _mandate_requirement_is_an_invariant(self) -> Self:
        if not self.require_valid_mandate_for_debit:
            raise ValueError(
                "require_valid_mandate_for_debit is an invariant and cannot be disabled"
            )
        return self


class Budgets(_Base):
    max_actions_per_episode: int = Field(ge=1)
    max_spend_per_episode_paise: int = Field(ge=0)
    max_concession_pct_of_amount: int = Field(ge=0, le=100)
    approval_required_above_paise: int = Field(ge=0)
    daily_org_spend_cap_paise: int = Field(ge=0)


class TriagePolicy(_Base):
    ev_floor_paise: int = Field(ge=0)
    action_window_hours: int = Field(default=336, ge=1, le=2160)
    """Uniform, cause-independent limit on how long an episode may be worked.
    Deliberately not per-cause: a per-cause window can only be computed from
    ground truth, and a harness that consults ground truth is scoring its own
    agent with information the agent never had."""


class MoneyPolicy(_Base):
    version: str
    money_actions: MoneyActionPolicy
    budgets: Budgets
    triage: TriagePolicy
    idempotency: dict[str, Any] = Field(default_factory=dict)


class Rung(_Base):
    rung: int = Field(ge=1)
    name: str
    channels: tuple[Channel, ...] = ()
    tone: str = "informational"
    min_days_since_previous: int = Field(default=0, ge=0)
    preconditions: tuple[str, ...] = ()
    terminal: bool = False


class EscalationPolicy(_Base):
    version: str
    skip_rungs: bool
    reset_on: tuple[str, ...]
    ladder: tuple[Rung, ...]

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.skip_rungs:
            raise ValueError("skip_rungs is an invariant and cannot be enabled")
        numbers = [r.rung for r in self.ladder]
        if numbers != sorted(numbers) or numbers != list(range(1, len(numbers) + 1)):
            raise ValueError(f"ladder rungs must be 1..N in order, got {numbers}")
        if not self.ladder[-1].terminal:
            raise ValueError("the final rung must be terminal (a human handoff)")
        return self

    def rung_at(self, n: int) -> Rung | None:
        return next((r for r in self.ladder if r.rung == n), None)


class PolicyBundle(_Base):
    """All policy, loaded and validated together."""

    contact: ContactPolicy
    money: MoneyPolicy
    escalation: EscalationPolicy

    @property
    def version(self) -> str:
        """Composite version stamped onto every gate decision in the audit log."""
        return f"{self.contact.version}+{self.money.version}+{self.escalation.version}"

    def unverified_rules(self) -> list[str]:
        """Rules not independently confirmed. Surfaced in the README and dashboard.

        Being explicit about what we have *not* verified is worth more than an
        unearned appearance of legal authority.
        """
        out = []
        for name, window in (
            ("quiet_hours.messaging", self.contact.quiet_hours.messaging),
            ("quiet_hours.voice", self.contact.quiet_hours.voice),
        ):
            if not window.verified:
                out.append(f"{name}: {window.source or 'no source recorded'}")
        return out


def load_policies(directory: str | Path = "policies") -> PolicyBundle:
    """Load and validate ``contact.yaml``, ``money.yaml`` and ``escalation.yaml``."""
    path = Path(directory)

    def _read(name: str) -> dict[str, Any]:
        f = path / name
        if not f.exists():
            raise FileNotFoundError(f"policy file missing: {f}")
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"policy file {f} must contain a mapping")
        return data

    return PolicyBundle(
        contact=ContactPolicy.model_validate(_read("contact.yaml")),
        money=MoneyPolicy.model_validate(_read("money.yaml")),
        escalation=EscalationPolicy.model_validate(_read("escalation.yaml")),
    )
