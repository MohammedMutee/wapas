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


_LEVEL_WORDING = {
    "full": "failures with exactly this rail, step, source and error code",
    "no_code": "failures with this rail, step and source",
    "surface_rail_step": "failures on this rail at this step",
    "surface_rail": "failures on this rail",
    "surface": "failures on this surface",
    "global": "all resolved failures",
}


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
    prior: tuple[list[tuple[object, float]], str] | None = None,
    neighbours: list[tuple[object, float]] | None = None,
    issuer_spiking: bool = False,
) -> str:
    """The episode-specific half. Deliberately narrow, and free of PII.

    ``prior`` and ``neighbours`` come from the merchant's resolved history.
    Both are evidence a deployed system genuinely has and earlier versions of
    this prompt withheld, which made the task harder than the real one.

    ``issuer_spiking`` is stated as a fact rather than a rate, deliberately.
    The model cannot use "6.2x normal" better than "far above normal", and
    quoting the number would give every episode a distinct prompt and make the
    diagnosis cache useless.

    Base rates are rounded to 5% on purpose. The model cannot use more
    precision than that, and rounding collapses hundreds of near-identical
    contexts onto the same prompt — which is what keeps the diagnosis cache
    small enough to warm.
    """
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

    if issuer_spiking:
        lines += [
            "",
            "OTHER TRAFFIC: this bank is currently failing far above its normal rate.",
            "  An outage is in progress. A failure on this bank right now is most likely",
            "  issuer_down even when its own error text says something else, unless that",
            "  text names a different mechanism outright (an expired card is still an",
            "  expired card during an outage).",
        ]

    if prior:
        distribution, level = prior
        top = [(c, round(share * 20) / 20) for c, share in distribution[:4]
               if round(share * 20) / 20 >= 0.05]
        if top:
            lines += [
                "",
                f"BASE RATES from this merchant's resolved history, over "
                f"{_LEVEL_WORDING.get(level, level)}:",
            ]
            lines += [f"  {share:.0%}  {cause}" for cause, share in top]
            lines.append(
                "  Use these when the text is uninformative. They are the best "
                "evidence available then, and better than `unknown`. Do NOT let "
                "them override text that plainly names a different mechanism."
            )

    if neighbours:
        lines += ["", "SIMILAR WORDINGS RESOLVED BEFORE (similarity, resolved cause):"]
        lines += [f"  {score:.2f}  {ex.cause}  \"{ex.description}\""
                  for ex, score in neighbours]
        lines.append(
            "  These are lexically similar, not necessarily the same failure. "
            "Weigh them against what the text actually says."
        )

    return "\n".join(lines)
