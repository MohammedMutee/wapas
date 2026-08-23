# Wapas — evaluation report

Seed `20260901` · episodes `5000` · policy `contact/3+money/4+escalation/3` · rates `rates/1` · sim `sim/3`

> **In-simulation results.** Every number below is produced by the synthetic
> world defined in `sim/params.yaml`, whose generative parameters are published
> and which the agent never reads. These are not measured Razorpay statistics.

> **The treatment arm.** The treatment arm runs the **LLM agent**. It shares the playbook library, the policy gate, the cost ledger and the audit chain with `baseline_rules`; the only difference between the two arms is how the root cause is classified. That is what makes the comparison an ablation of the model rather than of the system around it.

## Headline

| Metric | Value |
|---|---|
| Total revenue at risk | ₹1,16,43,570.53 |
| Gross recovered (treatment) | ₹23,41,075.59 |
| Control arm, untreated, scaled to treatment size | ₹5,36,526.77 |
| **Incremental recovery** | **₹18,04,548.81** (95% CI [₹14,98,071.70, ₹21,28,663.30], p = 0.0001) |
| Realised cost of treatment | ₹356.47 |
| **Net incremental recovery** | **₹18,04,192.34** |
| Modelled externalities (opt-outs, complaints, disputes) | less ₹2,71,279.73 |
| **Net after externalities** | **₹15,32,912.61** |
| Cost per ₹100 recovered | ₹0.02 |
| Policy denials (actions blocked before execution) | 139 |
| Policy modifications (rescheduled, not dropped) | 714 |
| Audit chain | chain intact: 39471 entries verified |

The control arm is the whole point. It recovered 14.9% of its episodes **without any intervention at all**. Reporting gross recovery would have claimed credit for every one of them.

## How this experiment is designed

| | |
|---|---|
| Allocation | stratified by amount decile, 5 arms, 40% / 15% / 15% / 15% / 15% |
| Decision rule | two-sided stratified permutation test at the 5% level |
| Interval | percentile bootstrap over episodes, 10,000 resamples |
| Null control | placebo split of the treatment arm, reported below |

Amounts are lognormal with a long right tail, so which arm happens to receive
the largest invoices matters more than any strategy does. Simple randomisation
balances that only in expectation; stratifying by amount decile balances it on
**every** run, to within one episode per decile. The permutation test then
shuffles labels within those same deciles, because that is the randomisation
the experiment actually performed.

Stratifying buys **precision, not correctness**. Both designs randomise, so
both reject a true null at the nominal rate; what stratifying removes is
variance, which narrows the bar a real effect has to clear. This distinction is
measured rather than asserted — see `results/calibration.md`, which also
records that the earlier A/A failure was an ordinary one-in-twenty event on one
seed rather than a broken procedure.

## Arms

Arms differ in size, so **totals are not comparable** — the per-episode
columns are the ones to read.

| Arm | n | Recovery rate | Gross / ep | Net / ep | Net after ext. / ep | Contacts / ep | Opt-out rate | Complaints |
|---|---|---|---|---|---|---|---|---|
| `treatment` | 2000 | 57.9% | ₹1,170.53 | ₹1,170.35 | ₹1,034.71 | 0.77 | 4.7% | 21 |
| `baseline_rules` | 750 | 61.1% | ₹1,031.93 | ₹1,031.73 | ₹728.33 | 0.85 | 5.7% | 12 |
| `baseline_naive` | 750 | 61.9% | ₹1,380.48 | ₹1,380.47 | ₹1,296.37 | 0.50 | 2.3% | 8 |
| `baseline_blast` | 750 | 55.3% | ₹1,037.01 | ₹1,036.66 | ₹835.71 | 1.00 | 6.1% | 11 |
| `control` | 750 | 14.9% | ₹268.26 | ₹268.26 | ₹268.26 | 0.00 | 0.0% | 0 |

### Treatment against each baseline

Difference in gross recovery per 1,000 episodes. The **p-value decides**;
the interval describes the size. A comparison is only as precise as its
smaller arm.

| Compared with | n | Δ gross / 1,000 ep | 95% CI | p | Claim supported? |
|---|---|---|---|---|---|
| `control` | 750 | ₹9,02,274.40 | [₹7,49,035.85, ₹10,64,331.65] | 0.0001 | yes |
| `baseline_naive` | 750 | -₹2,09,943.91 | [-₹5,18,480.95, ₹74,987.02] | 0.0792 | no — indistinguishable from noise |
| `baseline_blast` | 750 | ₹1,33,519.06 | [-₹1,09,019.09, ₹3,62,432.31] | 0.2357 | no — indistinguishable from noise |
| `baseline_rules` | 750 | ₹1,38,604.71 | [-₹74,564.34, ₹3,47,545.53] | 0.2063 | no — indistinguishable from noise  ← **A/A, see below** |

#### The same comparison on recovery rate

Rupees are what matter and rupees are heavy-tailed, so the interval above is
wide almost regardless of the strategy. Recovery rate is bounded and therefore
far more powerful at the same sample size. Both are reported; neither is
chosen after seeing the answer.

| Compared with | Δ recovery rate (pp) | 95% CI | p | Claim supported? |
|---|---|---|---|---|
| `control` | +42.92 | [+39.55, +46.23] | 0.0001 | yes |
| `baseline_naive` | -4.02 | [-8.12, +0.00] | 0.0546 | no — indistinguishable from noise |
| `baseline_blast` | +2.52 | [-1.65, +6.65] | 0.2383 | no — indistinguishable from noise |
| `baseline_rules` | -3.22 | [-7.30, +0.83] | 0.1253 | no — indistinguishable from noise |

#### Null controls — read these before believing any row above

**Placebo split.** The treatment arm is cut into two stratified halves (1000 / 1000 episodes) that ran the *same*
strategy on the *same* seed. The true difference is exactly zero by
construction, so this measures what the harness reports when there is nothing
to report. It stays valid after the LLM lands, which the
`treatment` vs `baseline_rules` row will not.

> Δ = -₹1,54,620.74 per 1,000 episodes, 95% CI [-₹4,42,215.42, ₹1,02,411.19], p = 0.2143 — correctly not significant

The noise floor for a comparison of this size is roughly ±₹4,42,215.42 per 1,000
episodes. A difference smaller than that is not a difference.

On recovery rate the same placebo gives -1.50 pp, p = 0.5342 — correctly not significant.

**Second A/A.** `treatment` and `baseline_rules` also run the same strategy today: p = 0.2063 — correctly not significant.

One seed cannot establish a false-positive *rate*. `make calibrate` runs the
placebo across many seeds and reports the measured rate against the nominal 5%;
see `results/calibration.md`.

### The aggression trade

`baseline_blast` recovers 55.3% of episodes against treatment's 57.9%, using 1.00 contacts per episode against 0.77, and produces an opt-out rate of 6.1% against 4.7%.

Channel spend cannot settle this argument. An SMS costs 12 paise and a
recovered invoice is worth thousands of rupees, so on a spend-only ledger the
optimal strategy is always to contact more. What actually disciplines contact
frequency is the revenue destroyed when a customer opts out, so that is now
priced — see `externalities` in `config/rates.yaml`.

| Arm | Gross / ep | Realised cost / ep | Externalities / ep | Net after ext. / ep |
|---|---|---|---|---|
| `treatment` | ₹1,170.53 | ₹0.17 | ₹135.63 | ₹1,034.71 |
| `baseline_blast` | ₹1,037.01 | ₹0.34 | ₹200.95 | ₹835.71 |
| `baseline_naive` | ₹1,380.48 | ₹0.00 | ₹84.09 | ₹1,296.37 |
| `baseline_rules` | ₹1,031.93 | ₹0.19 | ₹303.40 | ₹728.33 |

Treatment against blast on **net after externalities**: ₹1,99,006.42 per 1,000 episodes, 95% CI [-₹76,275.76, ₹4,68,229.73], p = 0.1324 — no — indistinguishable from noise.

**The externality figures are assumptions, not measurements**, and they are
the most contestable numbers in this project. They are reported on their own
line, and the net-before-externalities column is kept, so a reader who
rejects the model can still read every other result. The sensitivity sweep
varies them by ±30% like everything else.

## Diagnosis accuracy

Against the simulator's ground truth. Since `sim/signals.py` started emitting
realistic error text — several phrasings per cause, ISO 8583 response codes,
and 18% of failures carrying no
diagnostic text at all — this is a judgement call rather than a lookup. The
ceiling from text alone is roughly the informative share; anything above it has
to come from context.

| Arm | classified | correct | accuracy | on informative text | on uninformative |
|---|---|---|---|---|---|
| `treatment` | 2000 | 1509 | 75.4% | 89.5% (n=1645) | 10.4% (n=355) |
| `baseline_rules` | 750 | 560 | 74.7% | 85.7% (n=614) | 25.0% (n=136) |

### Accuracy is the wrong metric on an unanswerable question

The right-hand column above deserves more care than a percentage. When the
failure text says only "Transaction declined", the true cause is still a
specific mechanism, so a classifier that says `unknown` — the correct answer to
the question actually asked — is scored **wrong**. A classifier that guesses the
modal cause is scored right about a fifth of the time. On these episodes the
accuracy metric rewards guessing and penalises honesty, which is the same
mistake as D28 and this time it is in the scoring rather than the planner.

So the number to read instead: of 355 episodes whose text could not
identify the cause, the treatment arm said `unknown` on **132** (37%).
That is the behaviour worth having, and it costs accuracy points.

**206 diagnoses named a cause this simulator never generates**: `gateway_error` (206). This is not the same as abstaining, and it is not entirely the classifier's
fault either. The taxonomy is the *system's*, and it offers causes the world
model does not produce — `gateway_error` happens to real payment systems and
never happens here. A classifier cannot know that, so part of this count is a
gap in our simulator. The other part is not: reaching for `gateway_error` on a
bare "Transaction declined" names a specific mechanism the evidence does not
support, when `unknown` was available. Both are true, and the count is
reported rather than adjudicated.

The split is the interesting column. On text that names a mechanism, a keyword
table with the ISO 8583 codes in it is very hard to beat and a model has almost
nothing to add. The case for a model rests entirely on the right-hand column —
the failures where the answer has to be assembled from weak context rather than
looked up — and on that column being a large enough share of reality to matter.

## The model

| | |
|---|---|
| Model | `nvidia/nemotron-3-super-120b-a12b` |
| Fallback chain | `openai/gpt-oss-120b` |
| Diagnoses served | 2000 (2000 from cache, 0 live) |
| Fell back to rules | 0 (0.0%) |
| Stopped by the budget ceiling | 0 |
| Attempts per successful call | 0.00 |
| Tokens | 0 in, 0 out |
| Token cost (notional; free tier) | ₹0.00 |

Prompts are content-addressed and the cache is keyed on their digest, so a
second run of the same seed makes no calls at all and produces a byte-identical
report. Amounts reach the model as bands rather than exact figures, which is
what makes that collapse possible — and the prompt carries no personal data of
any kind, because nothing about diagnosing a decline requires knowing who the
customer is.

## What the model buys, and what it costs

The ablation. Same playbooks, same gate, same ledger, same audit chain;
the only difference between these two arms is who classifies the cause.

| | Model | Keyword classifier |
|---|---|---|
| Accuracy, text that names a mechanism | 89.5% | 85.7% |
| Accuracy, text that does not | 10.4% | 25.0% |
| Forbidden retries / 1,000 episodes | **33.5** | 48.0 |
| Recovery rate | 57.9% | 61.1% |
| Difference in recovery rate | -3.22 pp, p = 0.125 | — |

**It does not buy accuracy.** Overall the two are within a point of each
other. On text that names a mechanism the model is genuinely better, and on
text that does not, the accuracy metric punishes it for abstaining.

**It buys calibrated uncertainty the system can act on.** Forbidden retries
fall from 48 to 34 per 1,000 episodes, 30% fewer. A keyword table returns one
label. The model returns a label, a confidence, and what else it might have
been — and when the runner-up is a dead card or a risk decline, the gate
refuses the retry that a single confident-looking label would have allowed.
That rule is worth half the model arm's harm, and no regex can express its
input.

**It costs recovery.** -3.22 percentage points against
the keyword arm (p = 0.125, not significant, and inside the
placebo noise floor). Abstaining routes to the conservative playbook and the
runner-up rule blocks retries that would sometimes have worked. That is a
real trade and not a rounding error: **less revenue, less harm.** Which side
a merchant should want depends on how they price a retry against a dead
card, and this report deliberately does not decide that for them.

## Harm

What each strategy costs the people on the other end. **Forbidden retries** are
attempts against an episode whose *true* cause is never-retryable — a dead card,
a risk decline, a revoked mandate. The gate can only refuse a retry for a cause
somebody identified, so this is the price of a wrong diagnosis, and it is the
number a better diagnoser has to drive down.

| Arm | Forbidden retries / 1,000 ep | Opt-outs / 1,000 ep | Complaints / 1,000 ep | Disputes / 1,000 ep |
|---|---|---|---|---|
| `treatment` | 33.5 | 46.5 | 10.5 | 1.5 |
| `baseline_rules` | 48.0 | 57.3 | 16.0 | 5.3 |
| `baseline_naive` | 965.3 | 22.7 | 10.7 | 2.7 |
| `baseline_blast` | 453.3 | 61.3 | 14.7 | 6.7 |
| `control` | 0.0 | 0.0 | 0.0 | 0.0 |

`baseline_naive` does not diagnose at all, so it retries dead cards, risk
declines and revoked mandates indiscriminately — **29x** the rate of the
diagnosing arm. That is the harm the diagnosis step exists to prevent.

**And it still wins on money.** A forbidden retry is priced in
`config/rates.yaml` as amortised exposure to card-network decline-rate
monitoring, and at that price it does not come close to closing the gap. The
honest conclusion is that the case against the fixed ladder is a **compliance**
case, not a revenue case: it recovers more rupees, and it does so by doing
things a payments team would be unable to defend to an acquirer. Pricing the
penalty high enough to reverse the ranking would be fabrication, so the
ranking stands as measured and the argument is made on its actual grounds.

## Terminal states — the stopping rules, exercised

| State | Treatment | Naive | Blast | Control |
|---|---|---|---|---|
| `escalated` | 122 | 0 | 0 | 0 |
| `exhausted` | 488 | 268 | 291 | 0 |
| `partially_recovered` | 20 | 4 | 18 | 0 |
| `recovered` | 1137 | 460 | 397 | 112 |
| `skipped_negative_ev` | 0 | 0 | 0 | 638 |
| `suppressed` | 90 | 18 | 44 | 0 |
| `unrecoverable` | 143 | 0 | 0 | 0 |

## Per-cause recovery (treatment)

| Root cause | n | recovered | rate | gross |
|---|---|---|---|---|
| `insufficient_funds` | 375 | 240 | 64.0% | ₹3.40L |
| `mandate_insufficient` | 296 | 148 | 50.0% | ₹1.75L |
| `authentication_failed` | 244 | 148 | 60.7% | ₹2.41L |
| `invoice_forgotten` | 207 | 149 | 72.0% | ₹6.48L |
| `issuer_down` | 172 | 161 | 93.6% | ₹2.84L |
| `technical_timeout` | 118 | 84 | 71.2% | ₹91.0K |
| `mandate_revoked` | 116 | 55 | 47.4% | ₹88.0K |
| `invoice_cash_crunch` | 97 | 56 | 57.7% | ₹1.60L |
| `card_expired_or_invalid` | 91 | 39 | 42.9% | ₹62.7K |
| `invoice_disputed` | 85 | 11 | 12.9% | ₹1.57L |
| `limit_exceeded` | 74 | 43 | 58.1% | ₹59.3K |
| `risk_declined` | 71 | 7 | 9.9% | ₹16.3K |
| `customer_cancelled` | 54 | 16 | 29.6% | ₹19.3K |

## Known weaknesses

- Results are in-simulation. The sensitivity sweep (±30% on every parameter)
  is not yet implemented, so these numbers are one point in parameter space.
- The treatment arm runs the **LLM agent**. It shares the playbook library, the policy gate, the cost ledger and the audit chain with `baseline_rules`; the only difference between the two arms is how the root cause is classified. That is what makes the comparison an ablation of the model rather than of the system around it.
- Self-recovery is credited to whichever arm the episode fell in, including
  treatment. That is correct — it is exactly what the control arm subtracts —
  but it means the gross figure above is *not* the agent's achievement.
- Externality pricing is a model, not a measurement. See the aggression trade.
- Realised costs cover channel spend only. LLM token cost joins the ledger
  when the agent lands; free-tier models are priced notionally (see
  `config/rates.yaml`).

Reproduce: `make eval SEED=20260901`
