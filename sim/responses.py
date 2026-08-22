"""How a simulated counterparty responds to an intervention.

A documented logistic model. Every term is named and every coefficient lives in
``sim/params.yaml``, so the reader can see exactly what the agent is being
rewarded for. The structure matters more than the numbers:

* **Cause × intervention fit** is the signal a fixed retry ladder cannot
  express. Retrying an expired card is worthless; switching rails after an
  authentication drop is valuable. If this term were flat, cause-aware routing
  would be worth nothing and the agent would deserve to lose.
* **Timing fit** rewards retrying insufficient funds after payday rather than
  before, and rewards waiting out an issuer outage instead of hammering it.
* **Fatigue** makes each additional contact less effective *and* raises the
  opt-out hazard, so an aggressive strategy pays for itself in churn. This is
  what lets the evaluation show that the blast baseline wins gross and loses
  net.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass

from wapas.clock import IST
from wapas.domain import Channel, RootCause, Tool
from wapas.money import Paise

from .params import SimParams
from .populations import B2BBuyer, SeededEpisode
from .rng import Rng, logistic


@dataclass(frozen=True, slots=True)
class Interaction:
    """One agent action, as the simulated world sees it."""

    tool: Tool
    at: _dt.datetime
    channel: Channel = Channel.NONE
    concession_paise: Paise = Paise(0)
    contact_index: int = 0
    """How many contacts preceded this one. Drives fatigue."""


@dataclass(frozen=True, slots=True)
class Reaction:
    """What the world does back."""

    paid: bool
    amount_paise: Paise
    opted_out: bool
    complained: bool
    promised_until: _dt.datetime | None
    disputed: bool
    p_pay: float
    """The probability that produced this draw. Recorded for calibration checks."""


class ResponseModel:
    """Evaluates ``P(recover | ...)`` and draws an outcome."""

    def __init__(self, params: SimParams) -> None:
        self.p = params
        self.r = params.response

    # ── the log-odds accumulation ────────────────────────────────────────────

    def log_odds(
        self,
        episode: SeededEpisode,
        action: Interaction,
        *,
        issuer_down: bool,
    ) -> float:
        cp = episode.counterparty
        cause = episode.true_cause
        tool = str(action.tool)
        total = self.r.base_log_odds

        total += self.r.intervention_lift.get(tool, 0.0)
        total += self.r.cause_fit.get(str(cause), {}).get(tool, 0.0)
        total += self._timing(episode, action, issuer_down=issuer_down)

        if action.channel is not Channel.NONE and action.channel == cp.channel_preference:
            total += self.r.channel_fit_bonus

        # Responsiveness shifts the whole curve for this counterparty.
        total += (cp.responsiveness - 0.5) * 2.0

        if action.tool is Tool.OFFER_CONCESSION and action.concession_paise > 0:
            share = action.concession_paise / max(1, episode.amount_paise)
            total += self.r.concession_elasticity * cp.price_sensitivity * share * 10

        # Fatigue: each further contact is worth less than the last.
        total -= self.r.fatigue_lambda * action.contact_index

        if isinstance(cp, B2BBuyer):
            total += self._persona(cp, tool)

        return total

    def _timing(self, episode: SeededEpisode, action: Interaction, *, issuer_down: bool) -> float:
        t = self.r.timing
        cp, cause, bonus = episode.counterparty, episode.true_cause, 0.0

        if cause is RootCause.INSUFFICIENT_FUNDS and action.tool is Tool.RETRY_PAYMENT:
            local = action.at.astimezone(IST)
            refresh = getattr(cp, "liquidity_refresh_day", 1)
            days_since = (local.day - refresh) % 30
            if 0 <= days_since <= t.liquidity_window_days:
                bonus += t.liquidity_bonus
            else:
                bonus += t.liquidity_penalty

        if cause is RootCause.ISSUER_DOWN and action.tool is Tool.RETRY_PAYMENT:
            bonus += t.issuer_still_down_penalty if issuer_down else t.issuer_recovered_bonus

        local = action.at.astimezone(IST)
        if action.channel is not Channel.NONE and 9 <= local.hour < 18:
            bonus += t.business_hours_bonus
        return bonus

    def _persona(self, buyer: B2BBuyer, tool: str) -> float:
        match buyer.persona:
            case "prompt_payer":
                return 1.2
            case "cash_crunched":
                return 0.9 if tool == str(Tool.OFFER_CONCESSION) else -0.6
            case "disputer":
                return -1.5
            case "ghost":
                return -2.0
            case _:
                return 0.0

    # ── drawing an outcome ───────────────────────────────────────────────────

    def react(
        self,
        episode: SeededEpisode,
        action: Interaction,
        *,
        issuer_down: bool,
        rng: Rng,
        contacts_so_far: int,
    ) -> Reaction:
        """Draw the world's response to one action."""
        p_pay = logistic(self.log_odds(episode, action, issuer_down=issuer_down))
        cp = episode.counterparty

        is_contact = action.channel is not Channel.NONE
        opted_out = complained = disputed = False
        promised_until = None

        if is_contact:
            over = max(0, contacts_so_far + 1 - getattr(cp, "annoyance_threshold", 3))
            opt_hazard = self.r.opt_out_hazard_per_contact * (1 + 2 * over)
            opted_out = rng.child("optout").chance(min(0.9, opt_hazard))
            complained = rng.child("complain").chance(
                self.r.complaint_hazard_per_contact * (1 + over)
            )
            if isinstance(cp, B2BBuyer) and cp.persona == "disputer":
                disputed = rng.child("dispute").chance(cp.dispute_propensity)

        if opted_out or disputed:
            # A counterparty who has opted out or disputed does not then pay in
            # response to the same contact.
            return Reaction(False, Paise(0), opted_out, complained, None, disputed, p_pay)

        if rng.child("pay").chance(p_pay):
            if is_contact and rng.child("part").chance(self.r.part_payment_probability) and \
                    isinstance(cp, B2BBuyer):
                frac = rng.child("frac").uniform(
                    self.r.part_payment_fraction["min"], self.r.part_payment_fraction["max"]
                )
                return Reaction(True, Paise(int(episode.amount_paise * frac)), False,
                                complained, None, False, p_pay)
            return Reaction(True, episode.amount_paise, False, complained, None, False, p_pay)

        if is_contact and isinstance(cp, B2BBuyer) and \
                rng.child("promise").chance(self.r.promise_to_pay_probability):
            days = rng.child("phorizon").randint(
                self.r.promise_horizon_days["min"], self.r.promise_horizon_days["max"]
            )
            promised_until = action.at + _dt.timedelta(days=days)

        return Reaction(False, Paise(0), False, complained, promised_until, False, p_pay)


def probability_summary(model: ResponseModel, episode: SeededEpisode,
                        tools: tuple[Tool, ...], at: _dt.datetime) -> dict[str, float]:
    """P(pay) for each candidate tool. Used to sanity-check the world model."""
    return {
        str(tool): round(
            logistic(model.log_odds(
                episode,
                Interaction(tool=tool, at=at, channel=Channel.WHATSAPP
                            if tool is Tool.SEND_MESSAGE else Channel.NONE),
                issuer_down=False,
            )), 4
        )
        for tool in tools
    }


def _unused() -> None:  # pragma: no cover
    _ = math
