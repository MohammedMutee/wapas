"""The three comparison arms."""

from __future__ import annotations

import datetime as _dt

from ..domain import Channel, Diagnosis, ProposedAction, Tool
from .base import StrategyContext, unknown_diagnosis

H = _dt.timedelta(hours=1)


class DoNothing:
    """The randomised control arm.

    Takes no action of any kind. Whatever this arm recovers is revenue that
    would have arrived regardless — the counterfactual every other arm must be
    measured against. Without it, "we recovered ₹X" includes every customer who
    simply tried again, and means nothing.
    """

    name = "do_nothing"

    def diagnose(self, ctx: StrategyContext) -> Diagnosis | None:
        return None

    def next_action(self, ctx: StrategyContext) -> ProposedAction | None:
        return None


class NaiveRetry:
    """The industry default: a fixed ladder that ignores the failure reason.

    T+1h, T+24h, T+72h, plus one generic reminder. It will retry an expired
    card. It will hammer an issuer that is down. It will re-present the same
    3DS flow that the customer already abandoned. That is precisely the
    behaviour a cause-aware agent has to beat to justify existing.

    The reminder goes by **email**, not SMS. This is a fairness fix, not a
    cosmetic one: with SMS the reminder was denied on 352 of 750 episodes for
    ``no_channel_consent`` and ``channel_not_permitted_at_rung_1``, so the
    baseline never contacted anyone at all and lost on a technicality rather
    than on strategy. Email is universally consented in this simulation and is
    permitted at rung 1, so the naive ladder now actually gets to run. A
    baseline that cannot act is not a baseline.
    """

    name = "naive_retry"
    SCHEDULE = (1 * H, 24 * H, 72 * H)

    def diagnose(self, ctx: StrategyContext) -> Diagnosis | None:
        return None

    def next_action(self, ctx: StrategyContext) -> ProposedAction | None:
        if ctx.step_no < len(self.SCHEDULE):
            return ProposedAction(
                tool=Tool.RETRY_PAYMENT,
                scheduled_for=ctx.opened_at + self.SCHEDULE[ctx.step_no],
                rationale=f"fixed retry ladder, attempt {ctx.step_no + 1}",
            )
        if ctx.step_no == len(self.SCHEDULE):
            return ProposedAction(
                tool=Tool.SEND_MESSAGE,
                args={"channel": str(Channel.EMAIL), "rung": 1},
                scheduled_for=ctx.opened_at + 96 * H,
                rationale="generic reminder",
            )
        return None


class Blast:
    """Maximum aggression, to price what guardrails cost and what they buy.

    Every channel, immediately, repeatedly. This arm exists to be *allowed to
    win on gross recovery* — and then to lose on net once channel cost,
    opt-outs and complaints are counted. A single chart showing "aggression
    wins gross and loses net" justifies every rule in the policy engine.

    The policy gate still applies. Even the deliberately reckless arm cannot
    contact someone who has opted out or message at 3 a.m., because those are
    system invariants rather than strategy choices.
    """

    name = "blast"
    CHANNELS = (Channel.WHATSAPP, Channel.SMS, Channel.EMAIL, Channel.WHATSAPP,
                Channel.SMS, Channel.EMAIL, Channel.VOICE, Channel.WHATSAPP)

    def diagnose(self, ctx: StrategyContext) -> Diagnosis | None:
        return None

    def next_action(self, ctx: StrategyContext) -> ProposedAction | None:
        if ctx.step_no >= len(self.CHANNELS):
            return None
        if ctx.step_no % 3 == 2:
            return ProposedAction(
                tool=Tool.RETRY_PAYMENT,
                scheduled_for=ctx.now + _dt.timedelta(minutes=30),
                rationale="blast: retry regardless of cause",
            )
        return ProposedAction(
            tool=Tool.SEND_MESSAGE,
            args={"channel": str(self.CHANNELS[ctx.step_no]),
                  "rung": min(5, ctx.contacts_made + 1)},
            scheduled_for=ctx.now + _dt.timedelta(minutes=30),
            rationale="blast: contact on every channel, as often as permitted",
        )


def _unused() -> Diagnosis:  # pragma: no cover
    return unknown_diagnosis()
