"""The rules-only expert system.

Two jobs. It is the ``baseline_rules`` ablation that answers *"does the LLM
earn its cost?"*, and it is the deterministic fallback the agent degrades to
when a model call fails validation.

It is written to be genuinely good, because beating a strawman proves nothing.
That means it handles what a competent integration engineer would actually have
handled after a year of reading acquirer logs:

* prose in several phrasings per cause, not one canonical string
* **ISO 8583 response codes** — 51, 05, 54, 91, 59, 61 — which is what a real
  gateway returns when it returns anything at all
* NACH return reasons and UPI-specific wording
* buyer free-text on the receivables surface
* and, when the text says nothing at all, a **context fallback** over rail,
  step and surface rather than an immediate shrug

Where the LLM has to earn its place is the residue: text whose surface reading
points at the wrong cause, buyer replies that are polite and vague, and the
genuinely uninformative failures where the answer has to be assembled from
weak signals rather than looked up.
"""

from __future__ import annotations

import re

from ..domain import Diagnosis, ProposedAction, RootCause, Surface
from ..plan import playbook_for
from .base import StrategyContext

# ISO 8583 / acquirer response codes, which carry the cause exactly when the
# prose does not. Matched on a word boundary so "51" does not fire on "1519".
ISO_CODES: dict[str, tuple[RootCause, float]] = {
    "51": (RootCause.INSUFFICIENT_FUNDS, 0.94),
    "05": (RootCause.RISK_DECLINED, 0.70),   # do-not-honour: genuinely ambiguous
    "54": (RootCause.CARD_EXPIRED_OR_INVALID, 0.95),
    "91": (RootCause.ISSUER_DOWN, 0.93),
    "59": (RootCause.RISK_DECLINED, 0.92),
    "61": (RootCause.LIMIT_EXCEEDED, 0.92),
}

# Ordered: the first pattern that matches wins, so specific before general.
# `insufficient` must precede `declined`, because an insufficient-balance
# decline is both.
KEYWORD_RULES: tuple[tuple[tuple[str, ...], RootCause, float], ...] = (
    (("insufficient balance", "insufficient funds", "low balance",
      "not sufficient funds", "funds insufficient", "balance check failed"),
     RootCause.INSUFFICIENT_FUNDS, 0.92),
    (("mandate has been revoked", "revoked", "umrn not active", "mandate cancelled",
      "registration withdrawn"),
     RootCause.MANDATE_REVOKED, 0.90),
    (("auto-debit failed", "recurring debit bounced", "nach return"),
     RootCause.MANDATE_INSUFFICIENT, 0.85),
    (("3ds", "authentication", "otp", "did not complete", "acs", "bank page"),
     RootCause.AUTHENTICATION_FAILED, 0.86),
    (("not reachable", "issuer is down", "bank is down", "retry shortly",
      "inoperative", "upstream bank timeout", "bank as unavailable"),
     RootCause.ISSUER_DOWN, 0.88),
    (("timed out", "timeout", "status unknown", "indeterminate",
      "no final status", "reconciliation pending"),
     RootCause.TECHNICAL_TIMEOUT, 0.82),
    (("expired", "invalid card", "different payment method", "failed validation",
      "no longer valid"),
     RootCause.CARD_EXPIRED_OR_INVALID, 0.90),
    (("exceeds", "limit", "daily upi cap"), RootCause.LIMIT_EXCEEDED, 0.84),
    (("risk engine", "suspected fraud", "do not honour", "do not honor"),
     RootCause.RISK_DECLINED, 0.88),
    (("cancelled by the customer", "abandoned", "pressed back",
      "expired without approval", "cancelled"),
     RootCause.CUSTOMER_CANCELLED, 0.82),
    # ── receivables: what the buyer said ─────────────────────────────────────
    (("disputes the line items", "do not match", "billed twice", "under query",
      "disputes"),
     RootCause.INVOICE_DISPUTED, 0.88),
    (("cash constraints", "collections are slow", "funds are tight",
      "payment plan", "pay in two parts", "please hold"),
     RootCause.INVOICE_CASH_CRUNCH, 0.86),
    (("slipped through", "cannot find it", "resend the invoice",
      "reminders unanswered", "no response recorded", "past due date"),
     RootCause.INVOICE_FORGOTTEN, 0.72),
)

_CODE_PATTERN = re.compile(r"\b(0?5|51|54|59|61|91)\b")


def _iso_code(text: str) -> tuple[RootCause, float] | None:
    """Pull an issuer response code out of the text, if one is quoted.

    Only trusted when the surrounding text actually announces a code —
    "issuer response 51", "declined by issuing bank (05)" — because a bare
    two-digit number in prose is far more often an amount or a date.
    """
    if not any(marker in text for marker in
               ("response", "issuer response", "(0", "(5", "(6", "(9", "reason")):
        return None
    match = _CODE_PATTERN.search(text)
    if not match:
        return None
    return ISO_CODES.get(match.group(1).zfill(2))


def _from_context(ctx: StrategyContext) -> tuple[RootCause, float, str]:
    """No usable text. Guess from where in the flow it broke.

    Weak, and honestly labelled as weak by its confidence. But refusing to use
    the structured fields when the prose is empty would make the baseline worse
    than a real integration, and an easy baseline is not worth having.
    """
    if ctx.surface is Surface.RECEIVABLE:
        return RootCause.INVOICE_FORGOTTEN, 0.35, "no buyer signal; most overdue invoices are unnoticed"
    if ctx.surface is Surface.MANDATE:
        return RootCause.MANDATE_INSUFFICIENT, 0.40, "mandate debit failed with no reason given"
    if ctx.error_step == "authentication":
        return RootCause.AUTHENTICATION_FAILED, 0.45, "failed at the authentication step"
    if ctx.error_source == "issuer":
        return RootCause.INSUFFICIENT_FUNDS, 0.30, "issuer-side decline, no reason given; balance is the modal cause"
    return RootCause.UNKNOWN, 0.20, "no diagnostic signal in the failure"


_UNRECOVERABLE = {
    RootCause.RISK_DECLINED,
    RootCause.CARD_EXPIRED_OR_INVALID,
    RootCause.INVOICE_DISPUTED,
}


class RulesOnly:
    """Classification into the taxonomy, then the matching playbook.

    Given a :class:`~wapas.diagnose.history.ResolvedHistory` it uses it, and it
    should: for a fixed vocabulary of error strings a lookup over resolved
    outcomes is *optimal*, and a baseline denied that would be a strawman. The
    same history is available to the agent, so the comparison stays an ablation
    of the model rather than of who was allowed to remember things.
    """

    name = "rules_only"

    def __init__(self, history=None) -> None:
        self.history = history

    def diagnose(self, ctx: StrategyContext) -> Diagnosis:
        text = f"{ctx.error_description} {ctx.error_code}".lower()

        if self.history is not None:
            known = self.history.exact(ctx.error_description)
            if known is not None:
                cause, purity = known
                return self._diagnosis(
                    cause, min(0.97, purity),
                    [f"this exact wording resolved to {cause} in history"],
                    "resolved-history lookup",
                )

        coded = _iso_code(text)
        if coded is not None:
            cause, confidence = coded
            return self._diagnosis(cause, confidence,
                                   ["issuer response code quoted in the failure text"],
                                   "iso 8583 response code")

        for needles, cause, confidence in KEYWORD_RULES:
            if any(n in text for n in needles):
                return self._diagnosis(
                    cause, confidence,
                    [f"matched {next(n for n in needles if n in text)!r} in the failure text"],
                    "keyword classifier",
                )

        if self.history is not None:
            distribution, level = self.history.prior(
                surface=ctx.surface, rail=ctx.rail, step=ctx.error_step,
                source=ctx.error_source, code=ctx.error_code,
            )
            if distribution:
                cause, share = distribution[0]
                runner_up = distribution[1][0] if len(distribution) > 1 else None
                risk = self.history.riskiest_alternative(distribution)
                return self._diagnosis(
                    cause, min(0.6, share),
                    [f"no usable text; most common cause at this {level} is "
                     f"{cause} ({share:.0%} of history)"],
                    f"history prior [{level}]",
                    alternative=runner_up, risk=risk,
                )

        cause, confidence, why = _from_context(ctx)
        return self._diagnosis(cause, confidence, [why], "context fallback, no text match")

    def _diagnosis(
        self, cause: RootCause, confidence: float, evidence: list[str], notes: str,
        alternative: RootCause | None = None, risk: RootCause | None = None,
    ) -> Diagnosis:
        return Diagnosis(
            root_cause=cause,
            confidence=confidence,
            alternative_cause=alternative,
            risk_hypothesis=risk,
            evidence=evidence,
            recoverable=cause not in _UNRECOVERABLE,
            recommended_horizon_hours=72,
            notes=notes,
        )

    def next_action(self, ctx: StrategyContext) -> ProposedAction | None:
        diagnosis = ctx.diagnosis or self.diagnose(ctx)
        playbook = playbook_for(diagnosis.root_cause, ctx.surface)
        if ctx.step_no >= len(playbook.steps):
            return None
        return playbook.steps[ctx.step_no].to_action(ctx.opened_at, ctx.amount_paise)
