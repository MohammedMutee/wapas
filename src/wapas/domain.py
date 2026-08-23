"""The domain vocabulary: enumerations and value objects.

Everything here is a *closed set*. The diagnosis model classifies into
:class:`RootCause`; it cannot invent a new cause. The state machine moves
between members of :class:`EpisodeState`; it cannot invent a new state. Closed
sets are what make the policy gate provably exhaustive and the evaluation
tables complete.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .money import ZERO, Paise

# ─────────────────────────────────────────────────────────────────────────────
# Surfaces and events
# ─────────────────────────────────────────────────────────────────────────────


class Surface(StrEnum):
    """The three revenue-loss surfaces Wapas covers.

    Checkout abandonment is deliberately out of scope — see
    ``docs/scope.md`` for the reasoning.
    """

    PAYMENT = "payment"
    """A one-off payment attempt failed."""
    MANDATE = "mandate"
    """A recurring debit failed, or its mandate lapsed."""
    RECEIVABLE = "receivable"
    """A B2B invoice is overdue."""


class EventKind(StrEnum):
    """Normalised inbound event types. Provider payloads collapse into these."""

    PAYMENT_FAILED = "payment_failed"
    CHARGE_FAILED = "charge_failed"
    MANDATE_REVOKED = "mandate_revoked"
    INVOICE_OVERDUE = "invoice_overdue"


class Rail(StrEnum):
    """Payment rail. Determines which recovery actions are even possible."""

    CARD = "card"
    UPI = "upi"
    NETBANKING = "netbanking"
    WALLET = "wallet"
    EMANDATE = "emandate"
    BANK_TRANSFER = "bank_transfer"


# ─────────────────────────────────────────────────────────────────────────────
# Root cause taxonomy
# ─────────────────────────────────────────────────────────────────────────────


class RootCause(StrEnum):
    """Closed taxonomy of why revenue is at risk.

    The disposition of each cause — whether it is recoverable at all, and what
    must never be done about it — lives in :data:`DISPOSITIONS`. That table is
    consulted by the policy gate, so a wrong diagnosis can still not produce a
    forbidden action.
    """

    INSUFFICIENT_FUNDS = "insufficient_funds"
    AUTHENTICATION_FAILED = "authentication_failed"
    ISSUER_DOWN = "issuer_down"
    GATEWAY_ERROR = "gateway_error"
    TECHNICAL_TIMEOUT = "technical_timeout"
    CARD_EXPIRED_OR_INVALID = "card_expired_or_invalid"
    LIMIT_EXCEEDED = "limit_exceeded"
    RISK_DECLINED = "risk_declined"
    CUSTOMER_CANCELLED = "customer_cancelled"
    MANDATE_REVOKED = "mandate_revoked"
    MANDATE_INSUFFICIENT = "mandate_insufficient"
    INVOICE_DISPUTED = "invoice_disputed"
    INVOICE_FORGOTTEN = "invoice_forgotten"
    INVOICE_CASH_CRUNCH = "invoice_cash_crunch"
    UNKNOWN = "unknown"
    """Diagnosis could not classify. Forces the conservative playbook."""


class Disposition(BaseModel):
    """What the system is permitted and inclined to do about a root cause."""

    model_config = ConfigDict(frozen=True)

    recoverable: bool
    """False means no intervention can succeed; close the episode."""
    retry_allowed: bool
    """False means never re-present the same instruction. Enforced by the gate."""
    verify_before_retry: bool = False
    """True when the original outcome is ambiguous and a retry risks a double charge."""
    contact_customer: bool = True
    """False when the failure is not the customer's fault and messaging them is noise."""
    default_horizon_hours: int = 72
    """How long recovery is plausibly worth attempting."""
    note: str = ""


DISPOSITIONS: dict[RootCause, Disposition] = {
    RootCause.INSUFFICIENT_FUNDS: Disposition(
        recoverable=True, retry_allowed=True, default_horizon_hours=168,
        note="Time-dependent. Retry near the inferred liquidity refresh, not immediately.",
    ),
    RootCause.AUTHENTICATION_FAILED: Disposition(
        recoverable=True, retry_allowed=True, default_horizon_hours=48,
        note="Switch rails. Re-presenting the same 3DS flow repeats the same drop-off.",
    ),
    RootCause.ISSUER_DOWN: Disposition(
        recoverable=True, retry_allowed=True, contact_customer=False, default_horizon_hours=24,
        note="Not the customer's fault; messaging them is noise. Wait for issuer health.",
    ),
    RootCause.GATEWAY_ERROR: Disposition(
        recoverable=True, retry_allowed=True, contact_customer=False, default_horizon_hours=12,
    ),
    RootCause.TECHNICAL_TIMEOUT: Disposition(
        recoverable=True, retry_allowed=True, verify_before_retry=True,
        contact_customer=False, default_horizon_hours=6,
        note="Outcome unknown — the charge may have succeeded. Verify capture before any retry.",
    ),
    RootCause.CARD_EXPIRED_OR_INVALID: Disposition(
        recoverable=False, retry_allowed=False, default_horizon_hours=168,
        note="No retry can succeed. One request to update the instrument, then close.",
    ),
    RootCause.LIMIT_EXCEEDED: Disposition(
        recoverable=True, retry_allowed=True, default_horizon_hours=72,
        note="Same amount will fail again. Offer part-payment or an alternate rail.",
    ),
    RootCause.RISK_DECLINED: Disposition(
        recoverable=False, retry_allowed=False, contact_customer=False, default_horizon_hours=0,
        note="A risk decline is a decision, not an obstacle. Rail-shopping around it is abuse.",
    ),
    RootCause.CUSTOMER_CANCELLED: Disposition(
        recoverable=True, retry_allowed=False, default_horizon_hours=48,
        note="Intent signal. One low-friction nudge at most.",
    ),
    RootCause.MANDATE_REVOKED: Disposition(
        recoverable=True, retry_allowed=False, default_horizon_hours=336,
        note="Debit without a live mandate is hard-blocked. Reauthorisation only.",
    ),
    RootCause.MANDATE_INSUFFICIENT: Disposition(
        recoverable=True, retry_allowed=True, default_horizon_hours=336,
        note="Reschedule presentment; same-day re-presentment fails and costs a bounce.",
    ),
    RootCause.INVOICE_DISPUTED: Disposition(
        recoverable=False, retry_allowed=False, contact_customer=False, default_horizon_hours=0,
        note="Chasing a disputed invoice is harassment. Route to dispute intake and a human.",
    ),
    RootCause.INVOICE_FORGOTTEN: Disposition(
        recoverable=True, retry_allowed=False, default_horizon_hours=720,
    ),
    RootCause.INVOICE_CASH_CRUNCH: Disposition(
        recoverable=True, retry_allowed=False, default_horizon_hours=1440,
        note="Promise-to-pay and part-payment plans, slowly. Never threaten.",
    ),
    RootCause.UNKNOWN: Disposition(
        recoverable=True, retry_allowed=True, default_horizon_hours=24,
        note="Uncertainty degrades to caution, but a bounded retry of an already-"
             "authorised payment is the least risky money action available and is "
             "capped by max_retries and min_gap. Caution restricts concessions and "
             "escalation instead. Marking this non-retryable silently reduced every "
             "non-diagnosing baseline to inaction — a rigged comparison.",
    ),
}

NEVER_RETRY: frozenset[RootCause] = frozenset(
    c for c, d in DISPOSITIONS.items() if not d.retry_allowed
)
VERIFY_BEFORE_RETRY: frozenset[RootCause] = frozenset(
    c for c, d in DISPOSITIONS.items() if d.verify_before_retry
)
UNRECOVERABLE: frozenset[RootCause] = frozenset(
    c for c, d in DISPOSITIONS.items() if not d.recoverable
)


# ─────────────────────────────────────────────────────────────────────────────
# Episode lifecycle
# ─────────────────────────────────────────────────────────────────────────────


class EpisodeState(StrEnum):
    """States of a recovery episode.

    The eight terminal states are the stopping rules the track brief asks for.
    Each is individually reachable and individually tested.
    """

    # transient
    INGESTED = "ingested"
    TRIAGED = "triaged"
    DIAGNOSED = "diagnosed"
    PLANNED = "planned"
    GATED = "gated"
    ACTING = "acting"
    WAITING = "waiting"
    OBSERVED = "observed"
    PROMISED = "promised"
    """Suppressed until a promise-to-pay date; resumes afterwards."""

    # terminal
    RECOVERED = "recovered"
    PARTIALLY_RECOVERED = "partially_recovered"
    SKIPPED_NEGATIVE_EV = "skipped_negative_ev"
    UNRECOVERABLE = "unrecoverable"
    SUPPRESSED = "suppressed"
    EXHAUSTED = "exhausted"
    ESCALATED = "escalated"
    FAILED = "failed"
    """An internal error ended the episode. Counted honestly, never hidden."""


TERMINAL_STATES: frozenset[EpisodeState] = frozenset(
    {
        EpisodeState.RECOVERED,
        EpisodeState.PARTIALLY_RECOVERED,
        EpisodeState.SKIPPED_NEGATIVE_EV,
        EpisodeState.UNRECOVERABLE,
        EpisodeState.SUPPRESSED,
        EpisodeState.EXHAUSTED,
        EpisodeState.ESCALATED,
        EpisodeState.FAILED,
    }
)

VALID_TRANSITIONS: dict[EpisodeState, frozenset[EpisodeState]] = {
    EpisodeState.INGESTED: frozenset({EpisodeState.TRIAGED, EpisodeState.FAILED}),
    EpisodeState.TRIAGED: frozenset(
        {EpisodeState.DIAGNOSED, EpisodeState.SKIPPED_NEGATIVE_EV, EpisodeState.FAILED}
    ),
    EpisodeState.DIAGNOSED: frozenset(
        {EpisodeState.PLANNED, EpisodeState.UNRECOVERABLE, EpisodeState.FAILED}
    ),
    EpisodeState.PLANNED: frozenset({EpisodeState.GATED, EpisodeState.EXHAUSTED, EpisodeState.FAILED}),
    EpisodeState.GATED: frozenset(
        {EpisodeState.ACTING, EpisodeState.PLANNED, EpisodeState.EXHAUSTED, EpisodeState.FAILED}
    ),
    EpisodeState.ACTING: frozenset({EpisodeState.WAITING, EpisodeState.OBSERVED, EpisodeState.FAILED}),
    EpisodeState.WAITING: frozenset({EpisodeState.OBSERVED, EpisodeState.FAILED}),
    EpisodeState.OBSERVED: frozenset(
        {
            EpisodeState.PLANNED,
            EpisodeState.PROMISED,
            EpisodeState.RECOVERED,
            EpisodeState.PARTIALLY_RECOVERED,
            EpisodeState.SUPPRESSED,
            EpisodeState.EXHAUSTED,
            EpisodeState.ESCALATED,
            EpisodeState.UNRECOVERABLE,
            EpisodeState.FAILED,
        }
    ),
    EpisodeState.PROMISED: frozenset(
        {EpisodeState.OBSERVED, EpisodeState.PLANNED, EpisodeState.SUPPRESSED, EpisodeState.FAILED}
    ),
}
"""Explicit transition table. The engine refuses any move not listed here."""


class Arm(StrEnum):
    """Experiment arm. Assignment is deterministic from the episode seed.

    ``CONTROL`` receives no treatment whatsoever. It is what converts a demo
    into a measurement: a substantial share of failed payments recover on their
    own, and any system reporting gross recovery is claiming credit for them.
    """

    TREATMENT = "treatment"
    CONTROL = "control"
    BASELINE_NAIVE = "baseline_naive"
    BASELINE_BLAST = "baseline_blast"
    BASELINE_RULES = "baseline_rules"


# ─────────────────────────────────────────────────────────────────────────────
# Actions, outcomes, costs
# ─────────────────────────────────────────────────────────────────────────────


class Tool(StrEnum):
    """The actuator surface. Nothing outside this set can cause a side effect."""

    RETRY_PAYMENT = "retry_payment"
    CREATE_PAYMENT_LINK = "create_payment_link"
    REQUEST_MANDATE_REAUTH = "request_mandate_reauth"
    SEND_MESSAGE = "send_message"
    OFFER_CONCESSION = "offer_concession"
    PLACE_VOICE_CALL = "place_voice_call"
    RECORD_PROMISE_TO_PAY = "record_promise_to_pay"
    VERIFY_PAYMENT_CLAIM = "verify_payment_claim"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    CLOSE_EPISODE = "close_episode"


ALWAYS_ALLOWED: frozenset[Tool] = frozenset({Tool.ESCALATE_TO_HUMAN, Tool.CLOSE_EPISODE})
"""The exits are never gated. An agent that can be prevented from stopping is a bug."""

MONEY_ACTIONS: frozenset[Tool] = frozenset(
    {Tool.RETRY_PAYMENT, Tool.CREATE_PAYMENT_LINK, Tool.REQUEST_MANDATE_REAUTH, Tool.OFFER_CONCESSION}
)
CONTACT_ACTIONS: frozenset[Tool] = frozenset(
    {Tool.SEND_MESSAGE, Tool.PLACE_VOICE_CALL, Tool.REQUEST_MANDATE_REAUTH}
)


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    SMS = "sms"
    EMAIL = "email"
    VOICE = "voice"
    NONE = "none"


class OutcomeKind(StrEnum):
    PAYMENT_RECEIVED = "payment_received"
    PART_PAYMENT = "part_payment"
    PROMISE_TO_PAY = "promise_to_pay"
    MANDATE_REAUTHORISED = "mandate_reauthorised"
    DISPUTE_RAISED = "dispute_raised"
    OPT_OUT = "opt_out"
    COMPLAINT = "complaint"
    NO_RESPONSE = "no_response"


class AttributionMethod(StrEnum):
    """How a payment was linked to an action.

    ``UNATTRIBUTED`` payments are reported separately and never silently
    credited to the agent — the holdout arm exists precisely to expose that
    kind of over-claiming, including our own.
    """

    DIRECT_LINK = "direct_link"
    TIME_WINDOW = "time_window"
    UNATTRIBUTED = "unattributed"


class GateVerdict(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    MODIFY = "modify"
    """The action was rewritten to comply — e.g. a 22:10 message moved to 09:30."""


class CostKind(StrEnum):
    SMS = "sms"
    WHATSAPP = "whatsapp"
    VOICE = "voice"
    EMAIL = "email"
    LLM_TOKENS = "llm_tokens"
    GATEWAY_FEE = "gateway_fee"


# ─────────────────────────────────────────────────────────────────────────────
# Value objects
# ─────────────────────────────────────────────────────────────────────────────


def new_id() -> uuid.UUID:
    """Allocate an identifier.

    Note: ``uuid4`` is *not* used for anything that must be reproducible across
    runs. Simulation identifiers are derived from the run seed instead — see
    ``sim/populations.py``.
    """
    return uuid.uuid4()


class ProposedAction(BaseModel):
    """An action the planner wants to take, before the gate has seen it.

    This is the only shape an LLM may produce that leads to a side effect, and
    it must survive schema validation and then the policy gate before an
    actuator will look at it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: Tool
    args: dict[str, Any] = Field(default_factory=dict)
    scheduled_for: _dt.datetime | None = None
    rationale: str = ""
    playbook_step: str | None = None


class GateDecision(BaseModel):
    """The gate's ruling on a :class:`ProposedAction`."""

    model_config = ConfigDict(frozen=True)

    verdict: GateVerdict
    action: ProposedAction | None
    """The action to execute. ``None`` when denied; rewritten when modified."""
    reasons: tuple[str, ...] = ()
    """Machine-readable reason codes, e.g. ``("quiet_hours", "frequency_cap")``.
    Populated for allows too, so the audit log records what was checked."""
    policy_version: str = ""


class Diagnosis(BaseModel):
    """Structured output of the diagnosis step. Schema-enforced on the model call."""

    model_config = ConfigDict(extra="forbid")

    root_cause: RootCause
    confidence: float = Field(ge=0.0, le=1.0)
    alternative_cause: RootCause | None = None
    """The runner-up when the evidence is genuinely ambiguous.

    Load-bearing rather than decorative. A keyword table returns one label and
    no sense of what else it might have been; a model can say "probably A, but
    possibly B", and if B is something we must never retry then the safe action
    under that uncertainty is to not retry — even though A alone would permit
    it. That is the gate's ``alternative_cause_never_retryable`` rule, and it is
    the clearest thing structured model output buys that a classifier cannot.
    """
    risk_hypothesis: RootCause | None = None
    """A cause that must never be retried and that the evidence does not rule out.

    Distinct from ``alternative_cause``, which is the classifier's own
    second choice. This is the *safety* channel: it comes from the merchant's
    base rates for this context, and it answers a different question — not
    "what else might this be?" but "is there something here we would refuse to
    retry if we knew?".

    Keeping them separate was not the first design. Overloading
    ``alternative_cause`` for both meant that whenever the classifier named any
    runner-up of its own, the safety candidate was silently dropped — which is
    exactly the case where it mattered, and it left 25 forbidden retries
    standing on episodes diagnosed as `insufficient_funds` whose true cause was
    a dead card or a risk decline.
    """
    evidence: list[str] = Field(default_factory=list, max_length=5)
    recoverable: bool
    recommended_horizon_hours: int = Field(ge=0, le=2160)
    notes: str = ""

    @property
    def is_confident(self) -> bool:
        """Below this threshold the planner is forced onto the conservative playbook."""
        return self.confidence >= 0.5


class Budget(BaseModel):
    """Per-episode consumption counters. The gate refuses to exceed any of them."""

    model_config = ConfigDict(frozen=True)

    actions_used: int = 0
    contacts_used: int = 0
    spend_paise: Paise = ZERO
    retries_used: int = 0

    def with_action(
        self, *, contact: bool = False, retry: bool = False, cost: Paise = ZERO
    ) -> Budget:
        return Budget(
            actions_used=self.actions_used + 1,
            contacts_used=self.contacts_used + (1 if contact else 0),
            spend_paise=Paise(self.spend_paise + cost),
            retries_used=self.retries_used + (1 if retry else 0),
        )
