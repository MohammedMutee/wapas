# Compliance model

> **This is a good-faith engineering model of Indian norms for customer and
> borrower contact. It is not legal advice, and it is not verbatim regulation.**
> Citing a regulation incorrectly to a payments company would be worse than not
> citing one at all, so every rule in `policies/*.yaml` carries two fields:
>
> - `source:` — what the rule is modelled on
> - `verified:` — whether that has been independently confirmed
>
> Anything with `verified: false` is an engineering assumption. It is enforced
> by the code exactly as strictly as a confirmed rule, but it is never presented
> as a regulatory citation. `PolicyBundle.unverified_rules()` lists them, and
> the dashboard displays them.

## Why the gate is not a language model

A language model can be persuaded. A policy engine cannot.

Every action Wapas takes passes through deterministic Python that has no
prompt, no context window, and no capacity to be talked out of its rules. The
`wapas.policy` package contains no model call and never will.

This is what makes the red-team result a number (`0 escapes` across 20
adversarial scenarios) rather than an impression, and it is why that package is
held to `mypy --strict`.

## Invariants that no configuration can disable

These raise at policy-load time if a YAML file attempts to relax them:

| Invariant | Enforced in |
|---|---|
| A debit is never presented without a live mandate | `MoneyActionPolicy` |
| Escalation rungs cannot be skipped | `EscalationPolicy` |
| Third parties are never contacted, and debt is never disclosed to them | `ThirdPartyPolicy` |
| An unknown precondition fails closed | `PolicyGate._precondition_met` |
| The exits (`escalate_to_human`, `close_episode`) are never gated | `PolicyGate.evaluate` |

The last one matters more than it looks. An agent that can be *prevented from
stopping*, or from asking a human for help, is a bug — so those two tools
bypass every check including the kill switch, and they do not consume the
action budget.

## Deny, or modify?

The gate has three verdicts, and the middle one is the interesting one.

- **ALLOW** — execute as proposed. The reason codes still record what was
  checked, so the audit trail shows the action was examined rather than waved
  through.
- **MODIFY** — execute a rewritten action. A message scheduled at 22:10 IST
  becomes 08:00 the next morning. Rewriting beats dropping: the recovery still
  happens, and it happens legally.
- **DENY** — do not execute, and record why.

**Denied actions are never discarded.** They are written to the audit log and
counted on the dashboard, because the count of blocked actions is the evidence
that the cage is load-bearing.

## Data protection

Modelled on the Digital Personal Data Protection Act, 2023 (`verified: false`).

- Audit payloads store **salted digests, not raw personal data**
  (`wapas.audit.chain.redact`). The salt lives outside the chain, so a holder of
  the chain alone cannot reverse the digests.
- Phone numbers, emails, names, VPAs, card details and message bodies are all in
  `SENSITIVE_KEYS` and are digested automatically before hashing or storage.
- Non-sensitive fields — amounts, verdicts, timestamps, reason codes — stay
  readable, because an audit trail nobody can read is not an audit trail.

## Open compliance questions

Tracked, unresolved, and listed here rather than papered over:

1. Exact permitted contact-hour window for merchant-initiated collections, and
   whether recovery-agent norms bind a payment aggregator's merchant at all.
2. Whether transactional recovery messages fall inside or outside TRAI's
   unsolicited-commercial-communication regime.
3. Consent basis required for a voice call about a failed transaction, as
   distinct from an overdue invoice.
