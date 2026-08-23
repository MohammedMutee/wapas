"""The gate.

Every proposed action is evaluated here before an actuator sees it. The gate
returns one of three verdicts:

``ALLOW``
    Execute as proposed. The reason codes still record what was checked, so the
    audit trail shows the action was examined rather than waved through.

``MODIFY``
    Execute a rewritten action. The canonical case is a message scheduled at
    22:10 IST being moved to 09:30 the next morning. Rewriting is better than
    dropping: the recovery still happens, and it happens legally.

``DENY``
    Do not execute. The reason codes say why. **Denied actions are never
    discarded** — they are written to the audit log and counted on the
    dashboard, because the count of blocked actions is the evidence that the
    cage is load-bearing.

Ordering matters. Checks run cheapest-and-most-absolute first (kill switch,
consent, hard invariants) before anything that requires arithmetic, so a denial
reason is always the *most fundamental* reason the action was refused.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

from ..clock import IST, Clock
from ..domain import (
    ALWAYS_ALLOWED,
    CONTACT_ACTIONS,
    DISPOSITIONS,
    MONEY_ACTIONS,
    Channel,
    GateDecision,
    GateVerdict,
    ProposedAction,
    RootCause,
    Surface,
    Tool,
)
from ..money import ZERO, Paise
from .config import PolicyBundle, QuietWindow


@dataclass(frozen=True, slots=True)
class ContactRecord:
    """One past contact with this counterparty, used for frequency accounting."""

    at: _dt.datetime
    channel: Channel
    responded: bool = False


@dataclass(frozen=True, slots=True)
class GateContext:
    """Everything the gate needs to rule on an action.

    Deliberately a plain, fully-populated value object: the gate performs no
    I/O, so it is trivially unit-testable and its behaviour cannot depend on
    anything the caller did not make explicit.
    """

    now: _dt.datetime
    surface: Surface
    root_cause: RootCause
    amount_paise: Paise

    # what the diagnosis was unsure about
    alternative_cause: RootCause | None = None
    """The runner-up cause, when the classifier expressed one. A keyword table
    cannot produce this; a calibrated model can, and the gate uses it."""
    risk_hypothesis: RootCause | None = None
    """A never-retryable cause the base rates say is plausible here."""
    diagnosis_confidence: float = 1.0

    # consumption
    actions_used: int = 0
    contacts_used: int = 0
    spend_paise: Paise = ZERO
    retries_used: int = 0
    last_retry_at: _dt.datetime | None = None

    # counterparty state
    contact_history: tuple[ContactRecord, ...] = ()
    channel_consent: frozenset[Channel] = field(default_factory=frozenset)
    on_dnd_registry: bool = False
    opted_out: bool = False
    has_valid_mandate: bool = False
    is_business: bool = False

    # episode state
    escalation_rung: int = 0
    """Highest rung reached so far. 0 means nothing sent yet."""
    last_rung_at: _dt.datetime | None = None
    active_promise_until: _dt.datetime | None = None
    dispute_open: bool = False
    capture_verified: bool = False
    """Set once ``verify_payment_claim`` has confirmed the original outcome."""
    ledger_verified: bool = False

    # org-wide
    org_spend_today_paise: Paise = ZERO
    kill_switch_engaged: bool = False

    def contacts_since(self, since: _dt.datetime) -> int:
        return sum(1 for c in self.contact_history if c.at >= since)

    def voice_calls_since(self, since: _dt.datetime) -> int:
        return sum(
            1 for c in self.contact_history if c.at >= since and c.channel is Channel.VOICE
        )

    @property
    def last_contact(self) -> ContactRecord | None:
        return max(self.contact_history, key=lambda c: c.at, default=None)


def _next_allowed_time(when: _dt.datetime, window: QuietWindow) -> _dt.datetime:
    """Earliest instant at or after ``when`` that falls outside a quiet window.

    Computed in IST because the window is stated in local time, then returned in
    the original timezone.
    """
    local = when.astimezone(IST)
    if not window.contains(local.timetz().replace(tzinfo=None)):
        return when
    candidate = local.replace(
        hour=window.end.hour, minute=window.end.minute, second=0, microsecond=0
    )
    if candidate <= local:
        candidate += _dt.timedelta(days=1)
    return candidate.astimezone(when.tzinfo)


class PolicyGate:
    """Deterministic policy enforcement. No I/O, no randomness, no model calls."""

    def __init__(self, policies: PolicyBundle, clock: Clock | None = None) -> None:
        self.p = policies
        self.clock = clock

    # ── public API ───────────────────────────────────────────────────────────

    def evaluate(self, action: ProposedAction, ctx: GateContext) -> GateDecision:
        """Rule on a single proposed action."""
        checked: list[str] = []

        # The exits are never gated. An agent that can be prevented from
        # stopping, or from asking a human for help, is a bug.
        if action.tool in ALWAYS_ALLOWED:
            return self._allow(action, ("always_allowed",))

        if ctx.kill_switch_engaged:
            return self._deny(("kill_switch_engaged",))

        if (d := self._check_budgets(action, ctx, checked)) is not None:
            return d
        if action.tool in MONEY_ACTIONS and (d := self._check_money(action, ctx, checked)) is not None:
            return d
        if action.tool in CONTACT_ACTIONS:
            return self._check_contact(action, ctx, checked)

        return self._allow(action, tuple(checked))

    # ── budgets ──────────────────────────────────────────────────────────────

    def _check_budgets(
        self, action: ProposedAction, ctx: GateContext, checked: list[str]
    ) -> GateDecision | None:
        b = self.p.money.budgets
        checked.append("budget_actions")
        if ctx.actions_used >= b.max_actions_per_episode:
            return self._deny(("budget_actions_exhausted",))

        checked.append("budget_spend")
        if ctx.spend_paise >= b.max_spend_per_episode_paise:
            return self._deny(("budget_spend_exhausted",))

        checked.append("org_daily_cap")
        if ctx.org_spend_today_paise >= b.daily_org_spend_cap_paise:
            return self._deny(("org_daily_spend_cap",))
        return None

    # ── money actions ────────────────────────────────────────────────────────

    def _check_money(
        self, action: ProposedAction, ctx: GateContext, checked: list[str]
    ) -> GateDecision | None:
        m = self.p.money.money_actions
        b = self.p.money.budgets
        disposition = DISPOSITIONS[ctx.root_cause]

        if action.tool is Tool.RETRY_PAYMENT:
            # Invariant: never present a debit without a live mandate.
            checked.append("mandate_required")
            if not ctx.has_valid_mandate:
                return self._deny(("no_valid_mandate",))

            checked.append("never_retry_cause")
            if ctx.root_cause in m.never_retry_causes or not disposition.retry_allowed:
                return self._deny(("never_retry_cause", f"cause:{ctx.root_cause}"))

            # Under uncertainty, act safely for every cause still in play.
            #
            # A confident diagnosis of a retryable cause permits a retry. A
            # *hesitant* one that also names a never-retryable runner-up does
            # not: if the payment might be a dead card or a risk decline,
            # re-presenting it is exactly the action we would refuse if we
            # knew. Measured cost of not doing this: the model arm ran 66
            # forbidden retries per 1,000 episodes against the keyword arm's
            # 48, because honest `unknown` answers routed to a playbook that
            # retries.
            #
            # This rule is the one thing structured model output buys that a
            # keyword classifier cannot express. A regex returns one label; a
            # calibrated model returns a label, a confidence and what else it
            # might have been.
            if ctx.diagnosis_confidence < 0.75:
                for label, candidate in (("alt", ctx.alternative_cause),
                                         ("risk", ctx.risk_hypothesis)):
                    if candidate is not None and candidate in m.never_retry_causes:
                        return self._deny((
                            "alternative_cause_never_retryable",
                            f"{label}:{candidate}",
                            f"confidence:{ctx.diagnosis_confidence:.2f}",
                        ))

            checked.append("verify_before_retry")
            if ctx.root_cause in m.verify_before_retry_causes and not ctx.capture_verified:
                # Retrying an ambiguous timeout risks charging the customer twice.
                return self._deny(("capture_not_verified", "double_charge_risk"))

            checked.append("retry_count")
            if ctx.retries_used >= m.max_retries_per_payment:
                return self._deny(("max_retries_reached",))

            checked.append("retry_gap")
            if ctx.last_retry_at is not None:
                gap = _dt.timedelta(hours=m.min_gap_between_retries_hours)
                target = action.scheduled_for or ctx.now
                if target - ctx.last_retry_at < gap:
                    # Rewriting beats dropping: push the retry to the earliest legal moment.
                    return self._modify(
                        action.model_copy(update={"scheduled_for": ctx.last_retry_at + gap}),
                        ("retry_gap_too_short", "rescheduled"),
                    )

        if action.tool is Tool.REQUEST_MANDATE_REAUTH:
            checked.append("mandate_reauth_only_when_absent")
            if ctx.has_valid_mandate:
                return self._deny(("mandate_already_valid",))

        if action.tool is Tool.OFFER_CONCESSION:
            checked.append("concession_cap")
            value = _as_paise(action.args.get("value_paise", 0))
            max_value = ctx.amount_paise * b.max_concession_pct_of_amount // 100
            if value > max_value:
                return self._deny(
                    ("concession_exceeds_cap", f"max_paise:{max_value}", f"asked:{value}")
                )

            checked.append("approval_threshold")
            if ctx.amount_paise >= b.approval_required_above_paise and not action.args.get(
                "human_approved", False
            ):
                return self._deny(("human_approval_required",))

        return None

    # ── contact actions ──────────────────────────────────────────────────────

    def _check_contact(
        self, action: ProposedAction, ctx: GateContext, checked: list[str]
    ) -> GateDecision:
        c = self.p.contact
        channel = _as_channel(action.args.get("channel"))

        # Consent and suppression are absolute and come first.
        checked.append("opt_out")
        if ctx.opted_out:
            return self._deny(("opted_out", "permanent"))

        checked.append("dnd_registry")
        if c.consent.honour_dnd_registry and ctx.on_dnd_registry:
            return self._deny(("dnd_registry",))

        checked.append("channel_consent")
        if c.consent.require_channel_consent and channel not in ctx.channel_consent:
            return self._deny(("no_channel_consent", f"channel:{channel}"))

        checked.append("third_party")
        if action.args.get("recipient_is_third_party", False):
            return self._deny(("third_party_contact",))

        checked.append("dispute_open")
        if ctx.dispute_open:
            return self._deny(("dispute_open", "collections_paused"))

        checked.append("active_promise")
        if ctx.active_promise_until is not None and ctx.now < ctx.active_promise_until:
            return self._deny(("promise_to_pay_active",))

        checked.append("disposition_contact")
        if not DISPOSITIONS[ctx.root_cause].contact_customer:
            # e.g. issuer downtime: not the customer's fault, messaging is noise.
            return self._deny(("contact_not_indicated_for_cause", f"cause:{ctx.root_cause}"))

        # Escalation ladder.
        if (denial := self._check_ladder(action, ctx, channel, checked)) is not None:
            return denial

        # Frequency caps.
        if (denial := self._check_frequency(ctx, channel, checked)) is not None:
            return denial

        # Quiet hours last: it is the one check that rewrites rather than refuses.
        checked.append("quiet_hours")
        window = c.quiet_hours.for_channel(channel)
        target = action.scheduled_for or ctx.now
        allowed_at = _next_allowed_time(target, window)
        if allowed_at != target:
            return self._modify(
                action.model_copy(update={"scheduled_for": allowed_at}),
                ("quiet_hours", f"rescheduled_to:{allowed_at.astimezone(IST).isoformat()}"),
            )

        return self._allow(action, tuple(checked))

    def _check_ladder(
        self, action: ProposedAction, ctx: GateContext, channel: Channel, checked: list[str]
    ) -> GateDecision | None:
        e = self.p.escalation
        requested = int(action.args.get("rung", ctx.escalation_rung + 1))

        checked.append("rung_ordering")
        if requested > ctx.escalation_rung + 1:
            return self._deny(
                ("skip_rungs_forbidden", f"at:{ctx.escalation_rung}", f"requested:{requested}")
            )

        rung = e.rung_at(requested)
        if rung is None:
            return self._deny(("ladder_exhausted",))

        checked.append("rung_channel")
        if rung.channels and channel not in rung.channels:
            return self._deny((f"channel_not_permitted_at_rung_{requested}", f"channel:{channel}"))

        checked.append("rung_cooldown")
        if ctx.last_rung_at is not None and rung.min_days_since_previous:
            earliest = ctx.last_rung_at + _dt.timedelta(days=rung.min_days_since_previous)
            if ctx.now < earliest:
                return self._deny(("rung_cooldown_active", f"earliest:{earliest.isoformat()}"))

        checked.append("rung_preconditions")
        for pre in rung.preconditions:
            if not self._precondition_met(pre, ctx, channel):
                return self._deny((f"precondition_failed:{pre}",))
        return None

    def _precondition_met(self, pre: str, ctx: GateContext, channel: Channel) -> bool:
        match pre:
            case "channel_consent":
                return channel in ctx.channel_consent
            case "voice_consent":
                return Channel.VOICE in ctx.channel_consent
            case "no_active_dispute":
                return not ctx.dispute_open
            case "no_active_promise":
                return ctx.active_promise_until is None or ctx.now >= ctx.active_promise_until
            case "b2b_only":
                return ctx.is_business
            case "ledger_verified":
                return ctx.ledger_verified
            case "business_hours":
                local = ctx.now.astimezone(IST)
                return 9 <= local.hour < 18
            case s if s.startswith("min_amount_paise_"):
                return ctx.amount_paise >= int(s.rsplit("_", 1)[1])
            case _:
                # An unknown precondition fails closed. A typo in a policy file
                # must never widen what the agent is permitted to do.
                return False

    def _check_frequency(
        self, ctx: GateContext, channel: Channel, checked: list[str]
    ) -> GateDecision | None:
        f = self.p.contact.frequency_caps

        checked.append("cap_episode")
        if ctx.contacts_used >= f.contacts_per_episode:
            return self._deny(("episode_contact_cap",))

        checked.append("cap_day")
        if ctx.contacts_since(ctx.now - _dt.timedelta(days=1)) >= f.messages_per_day:
            return self._deny(("daily_contact_cap",))

        checked.append("cap_week")
        if ctx.contacts_since(ctx.now - _dt.timedelta(days=7)) >= f.messages_per_week:
            return self._deny(("weekly_contact_cap",))

        if channel is Channel.VOICE:
            checked.append("cap_voice_week")
            if ctx.voice_calls_since(ctx.now - _dt.timedelta(days=7)) >= f.voice_calls_per_week:
                return self._deny(("weekly_voice_cap",))

        checked.append("cooldown")
        last = ctx.last_contact
        if last is not None and not last.responded:
            cooldown = _dt.timedelta(hours=f.cooldown_after_no_response_hours)
            if ctx.now - last.at < cooldown:
                return self._deny(("cooldown_after_no_response",))
        return None

    # ── verdict helpers ──────────────────────────────────────────────────────

    def _allow(self, action: ProposedAction, reasons: tuple[str, ...]) -> GateDecision:
        return GateDecision(
            verdict=GateVerdict.ALLOW, action=action, reasons=reasons,
            policy_version=self.p.version,
        )

    def _deny(self, reasons: tuple[str, ...]) -> GateDecision:
        return GateDecision(
            verdict=GateVerdict.DENY, action=None, reasons=reasons,
            policy_version=self.p.version,
        )

    def _modify(self, action: ProposedAction, reasons: tuple[str, ...]) -> GateDecision:
        return GateDecision(
            verdict=GateVerdict.MODIFY, action=action, reasons=reasons,
            policy_version=self.p.version,
        )


def _as_channel(value: Any) -> Channel:
    if isinstance(value, Channel):
        return value
    try:
        return Channel(str(value))
    except ValueError:
        return Channel.NONE


def _as_paise(value: Any) -> Paise:
    return Paise(int(value)) if isinstance(value, (int, float, str)) and str(value).lstrip("-").isdigit() else Paise(0)
