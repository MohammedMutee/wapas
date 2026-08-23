"""Playbooks: cause → bounded action sequence.

This is the rules-only planner. It is deliberately written *well* rather than
as a strawman, because it doubles as ``baseline_rules`` — the ablation that
answers "does the LLM earn its cost?". A weak hand-written baseline would make
the agent look good by comparison and prove nothing.

Each step carries an offset from episode open, so a playbook expresses timing
as well as choice. The policy gate independently vets every step, so a playbook
cannot authorise something policy forbids — it can only propose.

**On retry counts.** Where a cause is genuinely retryable — a balance
shortfall, an issuer outage, a transient timeout — the playbook now uses more
of the retry budget policy already grants (3 attempts, 4 hours apart). It was
using one. A retry costs nothing, cannot annoy anyone, and carries no opt-out
hazard, so declining to use a permitted, harmless, effective action was not
caution, it was a weak planner: the fixed-ladder baseline was beating it on
``insufficient_funds`` 84% to 64% purely by retrying more.

Where a cause is *not* retryable the playbooks still refuse, and that refusal
is the point. Authentication drop-off, dead cards, limit breaches and risk
declines all get zero retries no matter how much budget is left.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from ..domain import Channel, ProposedAction, RootCause, Surface, Tool


@dataclass(frozen=True, slots=True)
class PlaybookStep:
    tool: Tool
    offset: _dt.timedelta
    channel: Channel = Channel.NONE
    rung: int | None = None
    rationale: str = ""
    concession_pct: int = 0
    """Percentage of the amount to give up. Capped independently by the gate."""

    def to_action(self, opened_at: _dt.datetime, amount_paise: int) -> ProposedAction:
        args: dict[str, object] = {}
        if self.channel is not Channel.NONE:
            args["channel"] = str(self.channel)
        if self.rung is not None:
            args["rung"] = self.rung
        if self.concession_pct:
            args["value_paise"] = amount_paise * self.concession_pct // 100
        return ProposedAction(
            tool=self.tool,
            args=args,
            scheduled_for=opened_at + self.offset,
            rationale=self.rationale,
        )


@dataclass(frozen=True, slots=True)
class Playbook:
    id: str
    steps: tuple[PlaybookStep, ...] = field(default_factory=tuple)


H = _dt.timedelta(hours=1)
D = _dt.timedelta(days=1)

PLAYBOOKS: dict[RootCause, Playbook] = {
    # Time the retry to the liquidity refresh, don't hammer it. One soft notice
    # first, because a customer who knows the payment failed often just pays.
    RootCause.INSUFFICIENT_FUNDS: Playbook("insufficient_funds_v1", (
        PlaybookStep(Tool.SEND_MESSAGE, 2 * H, Channel.WHATSAPP, rung=1,
                     rationale="Balance shortfall: notify, the customer may top up unprompted"),
        PlaybookStep(Tool.RETRY_PAYMENT, 3 * D,
                     rationale="Retry after the likely liquidity refresh, not before"),
        PlaybookStep(Tool.RETRY_PAYMENT, 5 * D,
                     rationale="Second attempt across the next refresh window"),
        PlaybookStep(Tool.CREATE_PAYMENT_LINK, 6 * D,
                     rationale="Offer an alternate rail if the retries fail"),
    )),

    # Never re-present the same 3DS flow; it drops out the same way twice.
    RootCause.AUTHENTICATION_FAILED: Playbook("authentication_failed_v1", (
        PlaybookStep(Tool.CREATE_PAYMENT_LINK, 1 * H,
                     rationale="Authentication drop-off: switch rails rather than repeat 3DS"),
        PlaybookStep(Tool.SEND_MESSAGE, 6 * H, Channel.WHATSAPP, rung=1,
                     rationale="Nudge with the low-friction link"),
    )),

    # Not the customer's fault. Wait out the outage, say nothing.
    RootCause.ISSUER_DOWN: Playbook("issuer_down_v1", (
        PlaybookStep(Tool.RETRY_PAYMENT, 2 * H,
                     rationale="Issuer outage: retry once the bank is likely back"),
        PlaybookStep(Tool.RETRY_PAYMENT, 8 * H, rationale="Second retry after a longer wait"),
        PlaybookStep(Tool.RETRY_PAYMENT, 1 * D,
                     rationale="Third and final retry; outages rarely outlast a day"),
    )),
    RootCause.GATEWAY_ERROR: Playbook("gateway_error_v1", (
        PlaybookStep(Tool.RETRY_PAYMENT, 1 * H, rationale="Transient gateway fault"),
    )),

    # Ambiguous outcome. The gate blocks the retry until capture is verified.
    RootCause.TECHNICAL_TIMEOUT: Playbook("technical_timeout_v1", (
        PlaybookStep(Tool.VERIFY_PAYMENT_CLAIM, _dt.timedelta(minutes=15),
                     rationale="Unknown final status: confirm no capture before retrying"),
        PlaybookStep(Tool.RETRY_PAYMENT, 2 * H, rationale="Safe to retry once verified"),
        PlaybookStep(Tool.RETRY_PAYMENT, 1 * D,
                     rationale="A transient fault can recur; one further attempt"),
    )),

    # No retry can succeed. Ask once for a new instrument, then stop.
    RootCause.CARD_EXPIRED_OR_INVALID: Playbook("card_expired_v1", (
        PlaybookStep(Tool.SEND_MESSAGE, 1 * H, Channel.WHATSAPP, rung=1,
                     rationale="Instrument is dead: ask for an update, never retry"),
        PlaybookStep(Tool.CREATE_PAYMENT_LINK, 2 * H,
                     rationale="Give them somewhere to pay with a different method"),
    )),

    RootCause.LIMIT_EXCEEDED: Playbook("limit_exceeded_v1", (
        PlaybookStep(Tool.CREATE_PAYMENT_LINK, 1 * H,
                     rationale="Per-transaction cap: offer another rail"),
        PlaybookStep(Tool.SEND_MESSAGE, 1 * D, Channel.WHATSAPP, rung=1,
                     rationale="Explain the bank limit and the alternative"),
    )),

    # A risk decline is a decision, not an obstacle.
    RootCause.RISK_DECLINED: Playbook("risk_declined_v1", (
        PlaybookStep(Tool.CLOSE_EPISODE, _dt.timedelta(0),
                     rationale="Risk decline: stop. Routing around it would be abuse."),
    )),

    RootCause.CUSTOMER_CANCELLED: Playbook("customer_cancelled_v1", (
        PlaybookStep(Tool.SEND_MESSAGE, 4 * H, Channel.WHATSAPP, rung=1,
                     rationale="Deliberate cancellation: one low-friction nudge only"),
    )),

    RootCause.MANDATE_REVOKED: Playbook("mandate_revoked_v1", (
        PlaybookStep(Tool.REQUEST_MANDATE_REAUTH, 2 * H, Channel.WHATSAPP,
                     rationale="No live mandate: reauthorisation is the only lawful path"),
        PlaybookStep(Tool.CREATE_PAYMENT_LINK, 2 * D,
                     rationale="One-off payment so this cycle is not lost"),
    )),
    RootCause.MANDATE_INSUFFICIENT: Playbook("mandate_insufficient_v1", (
        PlaybookStep(Tool.SEND_MESSAGE, 2 * H, Channel.WHATSAPP, rung=1,
                     rationale="Debit bounced: tell them before re-presenting"),
        PlaybookStep(Tool.RETRY_PAYMENT, 4 * D,
                     rationale="Re-present after the likely liquidity refresh"),
        PlaybookStep(Tool.RETRY_PAYMENT, 6 * D,
                     rationale="One further presentment; NACH allows a bounded re-try"),
    )),

    # ── receivables: the escalation ladder, one rung at a time ───────────────
    RootCause.INVOICE_FORGOTTEN: Playbook("invoice_forgotten_v1", (
        PlaybookStep(Tool.SEND_MESSAGE, 1 * H, Channel.EMAIL, rung=1,
                     rationale="Soft notice; most overdue invoices are simply unnoticed"),
        PlaybookStep(Tool.SEND_MESSAGE, 4 * D, Channel.WHATSAPP, rung=2,
                     rationale="Firm reminder after the rung-1 cooldown"),
        PlaybookStep(Tool.SEND_MESSAGE, 10 * D, Channel.EMAIL, rung=3,
                     rationale="Statement of account"),
        PlaybookStep(Tool.ESCALATE_TO_HUMAN, 20 * D, rationale="Ladder exhausted"),
    )),
    RootCause.INVOICE_CASH_CRUNCH: Playbook("invoice_cash_crunch_v1", (
        PlaybookStep(Tool.SEND_MESSAGE, 1 * H, Channel.EMAIL, rung=1,
                     rationale="Soft notice"),
        PlaybookStep(Tool.OFFER_CONCESSION, 5 * D, concession_pct=8,
                     rationale="Cash constrained: an instalment plan beats a chase"),
        PlaybookStep(Tool.SEND_MESSAGE, 12 * D, Channel.WHATSAPP, rung=2,
                     rationale="Firm reminder if the plan is not taken up"),
        PlaybookStep(Tool.ESCALATE_TO_HUMAN, 25 * D, rationale="Needs a human negotiation"),
    )),
    # Chasing a disputed invoice is harassment, not recovery.
    RootCause.INVOICE_DISPUTED: Playbook("invoice_disputed_v1", (
        PlaybookStep(Tool.ESCALATE_TO_HUMAN, _dt.timedelta(0),
                     rationale="Dispute raised: route to a human, stop collections"),
    )),

    # Uncertainty degrades to caution, never to guessing — but caution is not
    # the same as inaction, and this playbook had them confused.
    #
    # D16 established that `unknown` is retryable: a bounded retry of an
    # already-authorised payment is the least risky money action available, and
    # it is capped by max_retries and min_gap. The policy said so; this playbook
    # then never retried, so the stance and the planner disagreed and the
    # planner won. Caution belongs on the actions that can harm someone —
    # concessions, escalation, repeated contact — not on the one that cannot.
    #
    # This matters more since diagnosis got hard: 18% of failures carry no
    # diagnostic text, so `unknown` is now a common and often *correct* answer
    # rather than a rare admission of defeat. A planner that closes the episode
    # on it throws away every genuinely uncertain case, and it punishes an
    # honest classifier relative to one that guesses confidently.
    RootCause.UNKNOWN: Playbook("conservative_v1", (
        PlaybookStep(Tool.SEND_MESSAGE, 4 * H, Channel.EMAIL, rung=1,
                     rationale="Cause unclear: one informational notice"),
        PlaybookStep(Tool.RETRY_PAYMENT, 1 * D,
                     rationale="Bounded retry: the least risky money action, and the "
                               "gate still blocks it if the cause turns out non-retryable"),
        PlaybookStep(Tool.RETRY_PAYMENT, 3 * D,
                     rationale="Second and final attempt across a liquidity cycle"),
        PlaybookStep(Tool.CLOSE_EPISODE, 5 * D,
                     rationale="Do not guess with concessions or escalation"),
    )),
}

_SURFACE_FALLBACK: dict[Surface, RootCause] = {
    Surface.PAYMENT: RootCause.UNKNOWN,
    Surface.MANDATE: RootCause.MANDATE_INSUFFICIENT,
    Surface.RECEIVABLE: RootCause.INVOICE_FORGOTTEN,
}


def playbook_for(cause: RootCause, surface: Surface) -> Playbook:
    """Select a playbook, falling back conservatively by surface."""
    if cause in PLAYBOOKS:
        return PLAYBOOKS[cause]
    return PLAYBOOKS[_SURFACE_FALLBACK.get(surface, RootCause.UNKNOWN)]
