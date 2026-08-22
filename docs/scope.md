# Scope

## In scope — three surfaces, one engine

| Surface | Trigger | Why it earns its place |
|---|---|---|
| **A · Failed payments** | `payment.failed` | Largest immediate pool, richest failure taxonomy, hardest numbers. The correct response differs completely by cause, which is exactly what a fixed retry ladder cannot express. |
| **B · Mandates & subscriptions** | `subscription.charge.failed`, `mandate.revoked` | Recurring revenue fails silently. Recovery is time-critical and rail-specific (e-NACH vs UPI Autopay vs card-on-file behave differently) and involves reauthorisation, not just retries. |
| **C · B2B receivables** | `invoice.overdue` | Carries the escalation-ladder and compliance story. Distinguishing *cash-crunched* from *disputing* from *ghosting* is where judgment actually matters. |

All three run through the same detect → diagnose → decide → execute → measure
loop, the same policy language, and the same audit log. One engine, three
surfaces: the architecture is the argument.

## Deliberately out of scope

### Checkout abandonment

The track brief lists it. We are not building it, on purpose.

1. **Weakest ground truth of the five surfaces.** "Would they have bought
   anyway?" is unanswerable without a real merchant's traffic. Every number we
   could produce would be softer than the ones we can produce elsewhere, and
   the submission is judged on measurement integrity.
2. **Most saturated idea in the applicant pool.** Cart-recovery emails are the
   default hackathon project. There is no differentiation available there.
3. **Adding it would dilute, not extend.** It shares no new machinery with the
   three surfaces above — it is another event source and another playbook.

Declared cuts read as judgment. Silent omissions read as oversight.

### Fraud, returns and chargebacks

That is Track 02, whose brief warns that anything offence-capable is
disqualified. Wapas recovers revenue that a willing payer failed to complete.
It does not score, blacklist, or adjudicate anyone.

### Multi-tenancy, production hardening, live mode

Single merchant, single tenant, test mode only, clearly labelled.

## Anti-features — things we will not build even if asked

- Autonomous free-text negotiation with customers over money
- Contact with real consumers (synthetic identities and test mode only)
- Credit scoring or blacklisting of individuals
- Dark patterns: fake urgency, invented late fees, implied legal threats
- Debit without a live mandate, under any policy configuration
