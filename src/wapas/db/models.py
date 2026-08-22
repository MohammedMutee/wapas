"""ORM models.

Mirrors ``docs``/``ARCHITECTURE.md``. The shape worth noticing is that a
:class:`Decision` exists for **every brain cycle**, whether or not it produced
an action. Denied decisions are rows here, not discarded — the count of blocked
actions is the evidence that the policy gate is load-bearing.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, enum_str, fk_uuid, jsonb, paise, pk_uuid, ts


class Counterparty(Base):
    """The person or business who owes the money.

    Contact identifiers are stored here and **never** copied into the audit
    chain in the clear — the chain holds salted digests only.
    """

    __tablename__ = "counterparty"

    id: Mapped[uuid.UUID] = pk_uuid()
    external_ref: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    is_business: Mapped[bool] = mapped_column(Boolean, default=False)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)

    channel_consent: Mapped[list] = jsonb(default=list)
    on_dnd_registry: Mapped[bool] = mapped_column(Boolean, default=False)
    opted_out_at: Mapped[_dt.datetime | None] = ts(nullable=True)
    """Opt-out is permanent and propagates across every surface."""

    created_at: Mapped[_dt.datetime] = ts()
    episodes: Mapped[list[Episode]] = relationship(back_populates="counterparty")


class RiskEventRow(Base):
    """A normalised inbound event. Every provider payload collapses into this."""

    __tablename__ = "risk_event"

    id: Mapped[uuid.UUID] = pk_uuid()
    surface: Mapped[str] = enum_str(index=True)
    kind: Mapped[str] = enum_str(index=True)
    counterparty_id: Mapped[uuid.UUID] = fk_uuid("counterparty.id")

    amount_paise: Mapped[int] = paise()
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    occurred_at: Mapped[_dt.datetime] = ts(index=True)

    rail: Mapped[str | None] = enum_str(nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_step: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)

    provider_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    raw: Mapped[dict] = jsonb()
    """Untouched provider payload. Provenance matters for the audit story."""

    dedup_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    """Prevents a replayed webhook opening a second episode."""

    ingested_at: Mapped[_dt.datetime] = ts()


class Episode(Base):
    """One recovery attempt, from detection to a terminal state."""

    __tablename__ = "episode"

    id: Mapped[uuid.UUID] = pk_uuid()
    risk_event_id: Mapped[uuid.UUID] = fk_uuid("risk_event.id")
    counterparty_id: Mapped[uuid.UUID] = fk_uuid("counterparty.id")

    state: Mapped[str] = enum_str(index=True)
    arm: Mapped[str] = enum_str(index=True)
    """Experiment arm. `control` receives no treatment and is what turns a demo
    into a measurement."""

    surface: Mapped[str] = enum_str(index=True)
    amount_paise: Mapped[int] = paise()

    root_cause: Mapped[str | None] = enum_str(nullable=True, index=True)
    diagnosis_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_recover_prior: Mapped[float | None] = mapped_column(Float, nullable=True)
    ev_paise: Mapped[int | None] = paise(nullable=True, default=None)

    # Budget counters. The policy gate refuses to exceed any of them.
    actions_used: Mapped[int] = mapped_column(Integer, default=0)
    contacts_used: Mapped[int] = mapped_column(Integer, default=0)
    retries_used: Mapped[int] = mapped_column(Integer, default=0)
    spend_paise: Mapped[int] = paise()
    escalation_rung: Mapped[int] = mapped_column(Integer, default=0)

    last_retry_at: Mapped[_dt.datetime | None] = ts(nullable=True)
    last_rung_at: Mapped[_dt.datetime | None] = ts(nullable=True)
    active_promise_until: Mapped[_dt.datetime | None] = ts(nullable=True)
    dispute_open: Mapped[bool] = mapped_column(Boolean, default=False)
    capture_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    recovered_paise: Mapped[int] = paise()
    cost_paise: Mapped[int] = paise()

    opened_at: Mapped[_dt.datetime] = ts(index=True)
    closed_at: Mapped[_dt.datetime | None] = ts(nullable=True)
    terminal_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)

    seed: Mapped[int] = mapped_column(BigInteger, default=0)
    """Per-episode seed. Arm assignment and simulator draws derive from it, so a
    run is reproducible from the scenario alone."""

    counterparty: Mapped[Counterparty] = relationship(back_populates="episodes")
    decisions: Mapped[list[Decision]] = relationship(
        back_populates="episode", order_by="Decision.step_no"
    )
    outcomes: Mapped[list[Outcome]] = relationship(back_populates="episode")

    __table_args__ = (
        Index("ix_episode_arm_state", "arm", "state"),
        Index("ix_episode_cause_arm", "root_cause", "arm"),
    )

    @property
    def net_paise(self) -> int:
        return self.recovered_paise - self.cost_paise


class Decision(Base):
    """One brain cycle. Written whether or not it resulted in an action."""

    __tablename__ = "decision"

    id: Mapped[uuid.UUID] = pk_uuid()
    episode_id: Mapped[uuid.UUID] = fk_uuid("episode.id")
    step_no: Mapped[int] = mapped_column(Integer)
    node: Mapped[str] = enum_str(index=True)
    """`triage` | `diagnose` | `plan` | `gate` | `observe`"""

    input_digest: Mapped[str] = mapped_column(String(64))
    rationale: Mapped[str] = mapped_column(Text, default="")
    proposed_action: Mapped[dict | None] = jsonb(nullable=True, default=None)

    gate_verdict: Mapped[str | None] = enum_str(nullable=True, index=True)
    gate_reasons: Mapped[list] = jsonb(default=list)
    policy_version: Mapped[str] = mapped_column(String(80), default="")

    # LLM provenance. Null on deterministic nodes such as triage and gate.
    llm_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    llm_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    """Replay looks up cached responses by this, so a replay needs no network."""
    llm_response: Mapped[dict | None] = jsonb(nullable=True, default=None)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    llm_attempts: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[_dt.datetime] = ts()

    episode: Mapped[Episode] = relationship(back_populates="decisions")
    action: Mapped[Action | None] = relationship(back_populates="decision", uselist=False)

    __table_args__ = (Index("uq_decision_step", "episode_id", "step_no", unique=True),)


class Action(Base):
    """A side effect. One row per actuator invocation, idempotent by key."""

    __tablename__ = "action"

    id: Mapped[uuid.UUID] = pk_uuid()
    decision_id: Mapped[uuid.UUID] = fk_uuid("decision.id")
    episode_id: Mapped[uuid.UUID] = fk_uuid("episode.id")

    tool: Mapped[str] = enum_str(index=True)
    args: Mapped[dict] = jsonb()
    channel: Mapped[str | None] = enum_str(nullable=True)
    rung: Mapped[int | None] = mapped_column(Integer, nullable=True)

    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    """sha256(episode_id | step_no | tool | canonical_args). A duplicate insert
    is how a replayed webhook is prevented from charging someone twice."""

    scheduled_for: Mapped[_dt.datetime | None] = ts(nullable=True, index=True)
    executed_at: Mapped[_dt.datetime | None] = ts(nullable=True)
    status: Mapped[str] = enum_str(index=True)
    """`scheduled` | `sent` | `failed` | `cancelled` | `superseded`"""

    provider_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_response: Mapped[dict | None] = jsonb(nullable=True, default=None)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    decision: Mapped[Decision] = relationship(back_populates="action")


class CostEntry(Base):
    """Every rupee the agent spent trying to recover a rupee.

    Without this table the headline metric is gross, and gross recovery is the
    number that flatters everyone.
    """

    __tablename__ = "cost_entry"

    id: Mapped[uuid.UUID] = pk_uuid()
    episode_id: Mapped[uuid.UUID] = fk_uuid("episode.id")
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("action.id", ondelete="CASCADE"), nullable=True
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("decision.id", ondelete="CASCADE"), nullable=True
    )

    kind: Mapped[str] = enum_str(index=True)
    amount_paise: Mapped[int] = paise()
    unit_count: Mapped[int] = mapped_column(Integer, default=1)
    rate_ref: Mapped[str] = mapped_column(String(80), default="")
    """Version of config/rates.yaml that produced this figure."""
    notional: Mapped[bool] = mapped_column(Boolean, default=False)
    """True for free-tier model tokens priced at a market comparable. The report
    labels these so nobody is misled about what was actually paid."""

    created_at: Mapped[_dt.datetime] = ts()


class Outcome(Base):
    """What actually happened. The only source of recovered money."""

    __tablename__ = "outcome"

    id: Mapped[uuid.UUID] = pk_uuid()
    episode_id: Mapped[uuid.UUID] = fk_uuid("episode.id")

    kind: Mapped[str] = enum_str(index=True)
    amount_paise: Mapped[int] = paise()
    occurred_at: Mapped[_dt.datetime] = ts(index=True)

    attributed_to_action_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("action.id", ondelete="SET NULL"), nullable=True
    )
    attribution_method: Mapped[str] = enum_str()
    """`unattributed` payments are reported separately and never silently
    credited to the agent."""

    detail: Mapped[dict] = jsonb(default=dict)
    episode: Mapped[Episode] = relationship(back_populates="outcomes")


class AuditEntryRow(Base):
    """The hash chain, persisted.

    ``UPDATE`` and ``DELETE`` are rejected by a database trigger — see the
    initial migration. The tamper-evidence guarantee does not depend on
    application code behaving.
    """

    __tablename__ = "audit_entry"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[_dt.datetime] = ts(index=True)
    episode_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("episode.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    actor: Mapped[str] = enum_str(index=True)
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    payload: Mapped[dict] = jsonb()
    """Already redacted: sensitive values are salted digests, not plaintext."""

    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
