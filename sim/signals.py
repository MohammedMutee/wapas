"""What the failure actually looks like from outside.

Until now every root cause emitted exactly one canonical error string, and the
keyword classifier in ``wapas.strategies.rules`` matched it word for word. That
made diagnosis a lookup table, which is both unrealistic and self-defeating:
if a twenty-line regex table scores 100%, there is no headroom for a model and
no honest case for putting one in the loop.

Real acquirer text is nothing like that. The same decline arrives worded four
different ways depending on which bank and which acquirer handled it, often
compressed to an ISO 8583 response code with no prose at all, and a large
share of failures come back as some variant of "payment failed" carrying no
diagnostic information whatsoever. A recovery system's first real problem is
that it frequently *cannot tell* why the payment failed.

So the pools below are deliberately hostile:

* **Multiple phrasings per cause**, including terse coded ones.
* **ISO 8583 response codes** (51 insufficient funds, 05 do-not-honour, 54
  expired card, 91 issuer unavailable), because that is what a real integration
  gets and because a code is exactly the signal a keyword table handles badly.
* **Ambiguous variants** whose surface reading points at the wrong cause — a
  balance failure reported as a flat "declined by issuer" looks like a risk
  decline.
* **An uninformative pool** that carries no cause signal at all. When one of
  these is drawn the text simply cannot identify the cause, and the only
  remaining evidence is context: rail, step, amount, surface, and whether an
  outage happens to be in progress.

Everything here makes our own numbers worse. Diagnosis accuracy falls, the
rules baseline falls with it, and the report shows both. A benchmark that only
contains the easy cases is not measuring anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from wapas.domain import RootCause

from .rng import Rng


@dataclass(frozen=True, slots=True)
class ErrorSignal:
    code: str
    description: str
    source: str
    step: str
    informative: bool = True
    """False when the text alone cannot identify the cause. Recorded for the
    report, never shown to a strategy."""
    established: bool = True
    """True when this wording already appears in the merchant's resolved history.

    ``False`` marks a phrasing the system has never seen before — a new
    acquirer, a bank changing its wording, a failure mode that did not exist
    last quarter. This distinction is the whole reason a model is worth paying
    for. Given a fixed vocabulary of error strings, a lookup table over
    resolved history is *optimal* and an LLM is an expensive way to lose; the
    question worth asking is what happens the first time the text is new. So
    the history population is built from established phrasings only, the
    evaluation population uses all of them, and the report scores seen and
    novel wordings separately."""


_AUTH = "authorization"
_3DS = "authentication"

VARIANTS: dict[RootCause, tuple[ErrorSignal, ...]] = {
    RootCause.INSUFFICIENT_FUNDS: (
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Your card has insufficient balance to complete this payment",
                    "issuer", _AUTH),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Issuer response 51: NOT SUFFICIENT FUNDS", "issuer", _AUTH),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Transaction declined by issuing bank (05)", "issuer", _AUTH),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "UPI debit failed at remitter bank: balance check failed",
                    "issuer", _AUTH),
    ),
    RootCause.AUTHENTICATION_FAILED: (
        ErrorSignal("GATEWAY_ERROR",
                    "Customer did not complete 3DS authentication within the time limit",
                    "customer", _3DS),
        ErrorSignal("GATEWAY_ERROR",
                    "OTP not submitted; session expired on the ACS page",
                    "customer", _3DS),
        ErrorSignal("GATEWAY_ERROR",
                    "ACS returned status N for the authentication request",
                    "issuer", _3DS),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Payment failed at the bank page", "customer", _3DS),
    ),
    RootCause.ISSUER_DOWN: (
        ErrorSignal("GATEWAY_ERROR",
                    "The issuing bank is not reachable at the moment. Please retry shortly",
                    "issuer", _AUTH),
        ErrorSignal("GATEWAY_ERROR",
                    "Issuer response 91: ISSUER OR SWITCH INOPERATIVE", "issuer", _AUTH),
        ErrorSignal("GATEWAY_ERROR",
                    "Upstream bank timeout, no response received", "issuer", _AUTH),
        ErrorSignal("GATEWAY_ERROR",
                    "NPCI reported the remitter bank as unavailable", "issuer", _AUTH),
    ),
    RootCause.TECHNICAL_TIMEOUT: (
        ErrorSignal("GATEWAY_ERROR",
                    "Payment processing timed out; final status unknown",
                    "gateway", _AUTH),
        ErrorSignal("GATEWAY_ERROR",
                    "No final status received from the acquirer within 120s",
                    "gateway", _AUTH),
        ErrorSignal("GATEWAY_ERROR",
                    "Transaction in indeterminate state, reconciliation pending",
                    "gateway", _AUTH),
    ),
    RootCause.CARD_EXPIRED_OR_INVALID: (
        ErrorSignal("BAD_REQUEST_ERROR",
                    "The card has expired. Please use a different payment method",
                    "issuer", _AUTH),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Issuer response 54: EXPIRED CARD", "issuer", _AUTH),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Card number failed validation at the issuer", "issuer", _AUTH),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Instrument no longer valid on the network", "issuer", _AUTH),
    ),
    RootCause.LIMIT_EXCEEDED: (
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Transaction amount exceeds the per-transaction limit set by the bank",
                    "issuer", _AUTH),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Issuer response 61: EXCEEDS WITHDRAWAL AMOUNT LIMIT",
                    "issuer", _AUTH),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Daily UPI cap reached for this VPA", "issuer", _AUTH),
    ),
    RootCause.RISK_DECLINED: (
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Payment declined by the issuing bank risk engine", "issuer", _AUTH),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Issuer response 59: SUSPECTED FRAUD", "issuer", _AUTH),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Do not honour. Contact the card issuer.", "issuer", _AUTH),
    ),
    RootCause.CUSTOMER_CANCELLED: (
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Payment was cancelled by the customer on the bank page",
                    "customer", _3DS),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "User pressed back / abandoned the collect request",
                    "customer", _3DS),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Collect request expired without approval", "customer", _3DS),
    ),
    RootCause.MANDATE_REVOKED: (
        ErrorSignal("BAD_REQUEST_ERROR",
                    "The mandate for this subscription has been revoked by the customer",
                    "customer", _AUTH),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "UMRN not active: mandate cancelled at destination bank",
                    "issuer", _AUTH),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "e-NACH debit rejected, registration withdrawn", "issuer", _AUTH),
    ),
    RootCause.MANDATE_INSUFFICIENT: (
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Auto-debit failed: insufficient balance in the mandated account",
                    "issuer", _AUTH),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "NACH return reason 01: funds insufficient", "issuer", _AUTH),
        ErrorSignal("BAD_REQUEST_ERROR",
                    "Recurring debit bounced at destination bank", "issuer", _AUTH),
    ),
    # Surface C carries no gateway code — the signal is whatever the buyer said,
    # or silence.
    RootCause.INVOICE_FORGOTTEN: (
        ErrorSignal("", "Invoice past due date, no response recorded", "", ""),
        ErrorSignal("", "Buyer: sorry, this one slipped through, will look today", "", ""),
        ErrorSignal("", "Invoice overdue, two reminders unanswered", "", ""),
        ErrorSignal("", "Buyer: can you resend the invoice, we cannot find it", "", ""),
    ),
    RootCause.INVOICE_CASH_CRUNCH: (
        ErrorSignal("", "Invoice past due date, buyer reports cash constraints", "", ""),
        ErrorSignal("", "Buyer: our collections are slow this month, can we pay in two parts",
                    "", ""),
        ErrorSignal("", "Buyer: please hold till the 15th, funds are tight", "", ""),
        ErrorSignal("", "Invoice overdue, buyer requested a payment plan", "", ""),
    ),
    RootCause.INVOICE_DISPUTED: (
        ErrorSignal("", "Invoice past due date, buyer disputes the line items", "", ""),
        ErrorSignal("", "Buyer: the quantities on this invoice do not match the delivery note",
                    "", ""),
        ErrorSignal("", "Buyer: we were billed twice for the same shipment", "", ""),
        ErrorSignal("", "Invoice under query with the buyer's procurement team", "", ""),
    ),
}

# ── Wordings the keyword table has never been shown ──────────────────────────
#
# THE RULE, and it is the whole point: `KEYWORD_RULES` in
# ``wapas.strategies.rules`` is FROZEN with respect to everything below. No
# keyword may be added or edited to accommodate these strings, now or later.
# Check the git history if you doubt it.
#
# Without that rule the "novel phrasing" column measures nothing. The keyword
# table was written with every variant above visible on screen, so it scores
# 96.8% on them — not because keyword matching generalises, but because the
# author had already read the answers. A keyword table in production is written
# against last year's strings and meets this year's cold.
#
# These are written from payments domain knowledge as an acquirer would phrase
# the failure, deliberately without consulting the keyword table. Some of them
# will trip an existing keyword into the *wrong* answer — "Downstream financial
# institution timed out during authorisation" is an outage that says "timed
# out". That is not a trap, it is Tuesday.
NOVEL: dict[RootCause, ErrorSignal] = {
    RootCause.INSUFFICIENT_FUNDS: ErrorSignal(
        "BAD_REQUEST_ERROR", "Ledger balance below the presented amount",
        "issuer", _AUTH, established=False),
    RootCause.AUTHENTICATION_FAILED: ErrorSignal(
        "GATEWAY_ERROR", "Cardholder verification step not completed at the issuer page",
        "customer", _3DS, established=False),
    RootCause.ISSUER_DOWN: ErrorSignal(
        "GATEWAY_ERROR", "Downstream financial institution timed out during authorisation",
        "issuer", _AUTH, established=False),
    RootCause.TECHNICAL_TIMEOUT: ErrorSignal(
        "GATEWAY_ERROR", "Authorisation response not received before the cut-off",
        "gateway", _AUTH, established=False),
    RootCause.CARD_EXPIRED_OR_INVALID: ErrorSignal(
        "BAD_REQUEST_ERROR", "Presented card is past its good-thru date",
        "issuer", _AUTH, established=False),
    RootCause.LIMIT_EXCEEDED: ErrorSignal(
        "BAD_REQUEST_ERROR", "Amount above the ceiling configured by the cardholder's bank",
        "issuer", _AUTH, established=False),
    RootCause.RISK_DECLINED: ErrorSignal(
        "BAD_REQUEST_ERROR", "Blocked by the bank's transaction screening",
        "issuer", _AUTH, established=False),
    RootCause.CUSTOMER_CANCELLED: ErrorSignal(
        "BAD_REQUEST_ERROR", "Payer dismissed the approval request",
        "customer", _3DS, established=False),
    RootCause.MANDATE_REVOKED: ErrorSignal(
        "BAD_REQUEST_ERROR", "Standing instruction withdrawn by the account holder",
        "customer", _AUTH, established=False),
    RootCause.MANDATE_INSUFFICIENT: ErrorSignal(
        "BAD_REQUEST_ERROR", "Presentment returned unpaid for want of funds",
        "issuer", _AUTH, established=False),
    RootCause.INVOICE_FORGOTTEN: ErrorSignal(
        "", "Buyer: this went to the wrong inbox, chasing it internally now",
        "", "", established=False),
    RootCause.INVOICE_CASH_CRUNCH: ErrorSignal(
        "", "Buyer: we are waiting on a large receipt ourselves, need a few weeks",
        "", "", established=False),
    RootCause.INVOICE_DISPUTED: ErrorSignal(
        "", "Buyer: the rate card applied here is not what we agreed",
        "", "", established=False),
}


UNINFORMATIVE: tuple[ErrorSignal, ...] = (
    ErrorSignal("BAD_REQUEST_ERROR", "Payment failed", "", "", informative=False),
    ErrorSignal("GATEWAY_ERROR", "Transaction declined", "", "", informative=False),
    ErrorSignal("GATEWAY_ERROR", "Error at bank", "issuer", "", informative=False),
    ErrorSignal("BAD_REQUEST_ERROR", "Unable to process the payment at this time",
                "", "", informative=False),
    ErrorSignal("GATEWAY_ERROR", "PENDING_FAILURE", "gateway", "", informative=False),
)

UNINFORMATIVE_RECEIVABLE: tuple[ErrorSignal, ...] = (
    ErrorSignal("", "Invoice overdue", "", "", informative=False),
    ErrorSignal("", "No response from buyer", "", "", informative=False),
    ErrorSignal("", "Payment not received by due date", "", "", informative=False),
)


def draw_signal(
    rng: Rng,
    cause: RootCause,
    *,
    uninformative_share: float,
    established_only: bool = False,
) -> ErrorSignal:
    """Pick the error signal this episode presents.

    With probability ``uninformative_share`` the failure comes back with no
    diagnostic text at all — which is the case a recovery system has to handle
    well, not the case it gets to skip.

    ``established_only`` builds the resolved-history population: wordings the
    merchant has already seen and resolved. Leaving it False, as the evaluation
    does, mixes in phrasings that history has never contained.
    """
    variants = VARIANTS.get(cause)
    if not variants:
        return UNINFORMATIVE[0]
    if rng.chance(uninformative_share):
        pool = UNINFORMATIVE_RECEIVABLE if not variants[0].code else UNINFORMATIVE
        return rng.child("uninformative").choice(pool)
    novel = NOVEL.get(cause)
    if novel is not None and not established_only:
        variants = (*variants, novel)
    return rng.child("variant").choice(variants)
