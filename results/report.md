# Wapas — evaluation report

Seed `20260901` · episodes `2000` · policy `contact/3+money/3+escalation/3` · rates `rates/1` · sim `sim/1`

> **In-simulation results.** Every number below is produced by the synthetic
> world defined in `sim/params.yaml`, whose generative parameters are published
> and which the agent never reads. These are not measured Razorpay statistics.

> **Note on the current treatment arm.** The treatment arm currently runs the **rules-only** planner. The LLM agent is not yet wired in, so treatment and `baseline_rules` are the same policy differing only by sample. Any gap between them is sampling noise, and the LLM ablation is not yet meaningful.

## Headline

| Metric | Value |
|---|---|
| Total revenue at risk | ₹46,06,388.17 |
| Gross recovered (treatment) | ₹15,13,432.34 |
| Control arm, untreated, scaled to treatment size | ₹3,26,032.52 |
| **Incremental recovery** | **₹11,87,399.81** (95% CI [₹8,78,917.45, ₹15,23,226.80]) |
| Cost of treatment | ₹254.06 |
| **Net incremental recovery** | **₹11,87,145.75** |
| Cost per ₹100 recovered | ₹0.02 |
| Policy denials (actions blocked before execution) | 13 |
| Policy modifications (rescheduled, not dropped) | 444 |
| Audit chain | chain intact: 14844 entries verified |

The control arm is the whole point. It recovered 17.6% of its episodes **without any intervention at all**. Reporting gross recovery would have claimed credit for every one of them.

## Arms

Arms differ in size, so **totals are not comparable** — the per-episode
columns are the ones to read.

| Arm | n | Recovery rate | Gross / episode | Net / episode | Contacts / episode | Opt-out rate | Complaints |
|---|---|---|---|---|---|---|---|
| `treatment` | 1187 | 58.6% | ₹1,275.00 | ₹1,274.79 | 0.78 | 4.6% | 18 |
| `baseline_rules` | 209 | 58.9% | ₹890.35 | ₹890.12 | 0.85 | 3.3% | 1 |
| `baseline_naive` | 200 | 53.5% | ₹958.14 | ₹958.14 | 0.00 | 0.0% | 0 |
| `baseline_blast` | 188 | 60.6% | ₹1,273.71 | ₹1,273.36 | 0.99 | 4.8% | 5 |
| `control` | 216 | 17.6% | ₹274.66 | ₹274.66 | 0.00 | 0.0% | 0 |

### Treatment against each baseline

Incremental recovery per 1,000 episodes, with a 95% bootstrap CI over
episodes. A CI spanning zero means we cannot claim a difference.

| Compared with | Incremental / 1,000 episodes | 95% CI | Claim supported? |
|---|---|---|---|
| `control` | ₹10,00,336.83 | [₹7,40,452.78, ₹12,83,257.62] | yes |
| `baseline_naive` | ₹3,16,858.38 | [-₹89,310.83, ₹6,95,209.68] | no — CI spans zero |
| `baseline_blast` | ₹1,294.42 | [-₹3,98,079.19, ₹3,82,406.43] | no — CI spans zero |
| `baseline_rules` | ₹3,84,651.11 | [₹18,852.07, ₹7,32,436.86] | yes  ← **A/A test, see below** |

#### A/A sanity check — read this before believing any row above

`treatment` and `baseline_rules` currently run the **same strategy**. The true
difference between them is exactly zero, so that row is an A/A test and any
result other than "CI spans zero" is a **false positive**.

**On this seed the A/A test fails**: the interval excludes zero even though
there is nothing to detect. The cause is arm size — the baseline arms hold
~209 episodes against treatment's 1187 — combined with a heavy-tailed amount
distribution in which a few large recoveries move the mean a long way.

Consequences we accept rather than hide:

- The `baseline_naive` comparison above is the one that matters, and it
  **already spans zero**. We currently cannot claim to beat the industry
  default. Saying so now is cheaper than discovering it on camera.
- Before the final run: raise baseline arm sizes, stratify assignment by
  amount decile, and report the A/A interval alongside every A/B interval.

### The aggression trade

`baseline_blast` recovers 60.6% of episodes against treatment's 58.6%, using 0.99 contacts per episode against 0.78, and produces an opt-out rate of 4.8% against 4.6%.

**We are not yet able to show that guardrails pay for themselves.** Channel
spend is the only cost currently in the ledger, and at ₹254.06 across 1187 episodes it is far too small to
swing the net figure. The real cost of aggression is the *future* revenue from
a customer who opts out, and that is not priced yet. Until it is, the honest
statement is that blast wins gross and we cannot say what it loses.

## Diagnosis accuracy

| Arm | classified | correct | accuracy |
|---|---|---|---|
| `treatment` | 1187 | 1003 | 84.5% |
| `baseline_rules` | 209 | 180 | 86.1% |

## Terminal states — the stopping rules, exercised

| State | Treatment | Naive | Blast | Control |
|---|---|---|---|---|
| `escalated` | 82 | 0 | 0 | 0 |
| `exhausted` | 321 | 93 | 67 | 0 |
| `partially_recovered` | 9 | 0 | 1 | 0 |
| `recovered` | 687 | 107 | 113 | 38 |
| `skipped_negative_ev` | 0 | 0 | 0 | 178 |
| `suppressed` | 49 | 0 | 7 | 0 |
| `unrecoverable` | 39 | 0 | 0 | 0 |

## Per-cause recovery (treatment)

| Root cause | n | recovered | rate | gross |
|---|---|---|---|---|
| `insufficient_funds` | 228 | 146 | 64.0% | ₹1.96L |
| `mandate_insufficient` | 184 | 85 | 46.2% | ₹97.8K |
| `authentication_failed` | 134 | 90 | 67.2% | ₹1.25L |
| `invoice_forgotten` | 119 | 89 | 74.8% | ₹4.75L |
| `issuer_down` | 103 | 101 | 98.1% | ₹1.54L |
| `technical_timeout` | 74 | 42 | 56.8% | ₹57.4K |
| `mandate_revoked` | 65 | 32 | 49.2% | ₹60.6K |
| `invoice_cash_crunch` | 62 | 44 | 71.0% | ₹1.49L |
| `invoice_disputed` | 58 | 8 | 13.8% | ₹1.28L |
| `card_expired_or_invalid` | 53 | 25 | 47.2% | ₹16.5K |
| `limit_exceeded` | 39 | 28 | 71.8% | ₹39.8K |
| `risk_declined` | 39 | 0 | 0.0% | ₹0 |
| `customer_cancelled` | 29 | 6 | 20.7% | ₹13.6K |

## Known weaknesses

- Results are in-simulation. The sensitivity sweep (±30% on every parameter)
  is not yet implemented, so these numbers are one point in parameter space.
- The treatment arm currently runs the **rules-only** planner. The LLM agent is not yet wired in, so treatment and `baseline_rules` are the same policy differing only by sample. Any gap between them is sampling noise, and the LLM ablation is not yet meaningful.
- Self-recovery is credited to whichever arm the episode fell in, including
  treatment. That is correct — it is exactly what the control arm subtracts —
  but it means the gross figure above is *not* the agent's achievement.
- Costs currently cover channel spend only. LLM token cost joins the ledger
  when the agent lands; free-tier models are priced notionally (see
  `config/rates.yaml`).

Reproduce: `make eval SEED=20260901`
