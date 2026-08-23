"""Prompt construction for the diagnosis step.

Three deliberate choices.

**A stable prefix.** The taxonomy, the rules and the output contract are one
constant string, identical on every call, so a provider that supports prompt
caching bills most of it at the cached rate and a provider that does not at
least produces a stable ``prompt_hash`` for replay. Anything episode-specific
goes in the user message.

**No PII, ever.** The model sees the failure signal and coarse context. It
never sees a phone number, an email address, a name, or a payment reference.
There is nothing in a diagnosis that requires knowing who the customer is, so
sending it would be gratuitous exposure to a third-party API.

**Amounts are banded, not exact.** The band is all that matters for judging a
cause — a limit breach looks different at ₹200 and ₹80,000 — and banding turns
thousands of unique prompts into a few hundred, which makes the diagnosis cache
effective and the evaluation cheap and reproducible.
"""

from __future__ import annotations

from ..domain import DISPOSITIONS, RootCause, Surface
from ..money import Paise

_CAUSE_LINES = "\n".join(
    f"- {cause.value}: {'retryable' if DISPOSITIONS[cause].retry_allowed else 'NOT retryable'}"
    f"{'' if DISPOSITIONS[cause].recoverable else ', not recoverable'}"
    for cause in RootCause
)

SYSTEM = f"""\
You classify failed payment and receivable episodes for an Indian payments \
recovery system. You are given the machine-readable failure signal a payment \
gateway produced, plus coarse context. You return exactly one root cause from \
a fixed taxonomy.

TAXONOMY (use these exact strings):
{_CAUSE_LINES}

HOW TO DECIDE

1. Read the failure text literally first. Indian acquirers quote ISO 8583 \
response codes: 51 is insufficient funds, 05 is do-not-honour (generic, often \
a risk decline but sometimes a balance problem), 54 is an expired card, 91 is \
the issuer or switch being unavailable, 59 is suspected fraud, 61 is a \
withdrawal limit. NACH return reason 01 is insufficient funds.

2. Distinguish causes that look alike. A payment abandoned on the bank page is \
authentication_failed if the customer ran out of time or never submitted the \
OTP, and customer_cancelled if they actively cancelled or let a collect \
request lapse. A balance failure on a recurring debit is \
mandate_insufficient, not insufficient_funds; a debit refused because the \
registration is gone is mandate_revoked.

3. THE MOST IMPORTANT RULE. Some failures carry no diagnostic information at \
all: "Payment failed", "Transaction declined", "Error at bank", "Unable to \
process the payment at this time", "PENDING_FAILURE". These strings are \
consistent with almost every cause in the taxonomy and they identify none of \
them.

For these you must answer `unknown` with a confidence of 0.4 or lower, unless \
the structured context genuinely narrows it — a failure at the authentication \
step points to authentication_failed; a mandate surface points to a mandate \
cause. Even then, stay below 0.6.

Do NOT reach for `gateway_error` or `technical_timeout` as a way of avoiding \
`unknown`. `gateway_error` means the gateway itself reported a fault, and \
`technical_timeout` means the final status is genuinely unknown to the \
gateway; neither is a synonym for "the text did not say". Answering a specific \
cause at high confidence when the evidence is a bare "Transaction declined" is \
the single worst failure mode available to you, because the planner will act \
on it.

4. On the receivable surface there is no gateway code, only what the buyer \
said or did not say. Vague apologies and silence are invoice_forgotten. Any \
mention of tight cash, slow collections, instalments or delay requests is \
invoice_cash_crunch. Any challenge to the amount, quantities, duplication or \
delivery is invoice_disputed.

5. Be calibrated. Confidence is the probability you are right, not how firmly \
you would like to answer. A confident wrong classification causes a real harm \
downstream: this system refuses to retry causes such as risk_declined, \
card_expired_or_invalid and mandate_revoked, and a misclassification is \
exactly how a forbidden retry gets executed.

OUTPUT ORDER MATTERS. Grade `signal_quality` first, before you decide the \
cause: 'specific' if the text names a mechanism, 'weak' if it only hints at \
one, 'generic' if it merely says something failed. A 'generic' signal caps \
your confidence at 0.5 and a 'weak' one at 0.75; answers that break those caps \
are rejected and sent back to you.

Quote the specific words you relied on in `evidence`. If the signal is \
ambiguous, name the runner-up in `alternative_cause`.
"""


def _band(amount: Paise) -> str:
    rupees = int(amount) / 100
    if rupees < 500:
        return "under Rs 500"
    if rupees < 2_000:
        return "Rs 500 to Rs 2,000"
    if rupees < 10_000:
        return "Rs 2,000 to Rs 10,000"
    if rupees < 50_000:
        return "Rs 10,000 to Rs 50,000"
    return "over Rs 50,000"


def build_user_prompt(
    *,
    surface: Surface,
    rail: str,
    error_code: str,
    error_description: str,
    error_source: str,
    error_step: str,
    amount_paise: Paise,
    is_business: bool,
) -> str:
    """The episode-specific half. Deliberately narrow, and free of PII."""
    lines = [
        "FAILURE SIGNAL",
        f"  surface:      {surface}",
        f"  rail:         {rail or 'unknown'}",
        f"  error_code:   {error_code or '(none)'}",
        f"  description:  {error_description or '(none)'}",
        f"  source:       {error_source or '(not reported)'}",
        f"  failed_at:    {error_step or '(not reported)'}",
        "",
        "CONTEXT",
        f"  amount band:  {_band(amount_paise)}",
        f"  counterparty: {'business' if is_business else 'consumer'}",
    ]
    return "\n".join(lines)
