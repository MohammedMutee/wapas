"""The rules-only expert system.

Two jobs. It is the ``baseline_rules`` ablation that answers *"does the LLM
earn its cost?"*, and it is the deterministic fallback the agent degrades to
when a model call fails validation.

It is written to be genuinely good — a keyword classifier over the gateway
error text, feeding the same playbook library the agent uses. Beating a
strawman would prove nothing, so this baseline is built to be hard to beat.
Where the LLM should win is the long tail: error strings this classifier has
never seen, ambiguous multi-signal cases, and free-text replies from buyers.
"""

from __future__ import annotations

from ..domain import Diagnosis, ProposedAction, RootCause, Surface
from ..plan import playbook_for
from .base import StrategyContext

# Ordered: the first pattern that matches wins, so put the specific before the
# general. `insufficient` must be checked before `declined`, because an
# insufficient-balance decline is both.
KEYWORD_RULES: tuple[tuple[tuple[str, ...], RootCause, float], ...] = (
    (("insufficient balance", "insufficient funds", "low balance"),
     RootCause.INSUFFICIENT_FUNDS, 0.92),
    (("3ds", "authentication", "otp", "did not complete"),
     RootCause.AUTHENTICATION_FAILED, 0.88),
    (("not reachable", "issuer is down", "bank is down", "retry shortly"),
     RootCause.ISSUER_DOWN, 0.85),
    (("timed out", "timeout", "status unknown"), RootCause.TECHNICAL_TIMEOUT, 0.80),
    (("expired", "invalid card", "different payment method"),
     RootCause.CARD_EXPIRED_OR_INVALID, 0.90),
    (("exceeds", "limit"), RootCause.LIMIT_EXCEEDED, 0.82),
    (("risk engine", "risk", "suspected fraud"), RootCause.RISK_DECLINED, 0.86),
    (("cancelled by the customer", "cancelled"), RootCause.CUSTOMER_CANCELLED, 0.84),
    (("mandate has been revoked", "revoked"), RootCause.MANDATE_REVOKED, 0.90),
    (("auto-debit failed",), RootCause.MANDATE_INSUFFICIENT, 0.85),
    (("disputes the line items", "disputes"), RootCause.INVOICE_DISPUTED, 0.88),
    (("cash constraints",), RootCause.INVOICE_CASH_CRUNCH, 0.85),
    (("past due date",), RootCause.INVOICE_FORGOTTEN, 0.70),
)


class RulesOnly:
    """Keyword classification into the taxonomy, then the matching playbook."""

    name = "rules_only"

    def diagnose(self, ctx: StrategyContext) -> Diagnosis:
        text = f"{ctx.error_description} {ctx.error_code}".lower()
        for needles, cause, confidence in KEYWORD_RULES:
            if any(n in text for n in needles):
                return Diagnosis(
                    root_cause=cause,
                    confidence=confidence,
                    evidence=[f"matched {needles[0]!r} in the gateway error text"],
                    recoverable=cause not in {RootCause.RISK_DECLINED,
                                              RootCause.CARD_EXPIRED_OR_INVALID,
                                              RootCause.INVOICE_DISPUTED},
                    recommended_horizon_hours=72,
                    notes="keyword classifier",
                )
        # No rule matched. Degrade to caution, not to a guess.
        fallback = {
            Surface.MANDATE: RootCause.MANDATE_INSUFFICIENT,
            Surface.RECEIVABLE: RootCause.INVOICE_FORGOTTEN,
        }.get(ctx.surface, RootCause.UNKNOWN)
        return Diagnosis(
            root_cause=fallback, confidence=0.25, evidence=["no keyword rule matched"],
            recoverable=True, recommended_horizon_hours=24,
            notes="unmatched error text; conservative fallback",
        )

    def next_action(self, ctx: StrategyContext) -> ProposedAction | None:
        diagnosis = ctx.diagnosis or self.diagnose(ctx)
        playbook = playbook_for(diagnosis.root_cause, ctx.surface)
        if ctx.step_no >= len(playbook.steps):
            return None
        return playbook.steps[ctx.step_no].to_action(ctx.opened_at, ctx.amount_paise)
