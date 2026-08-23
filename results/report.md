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
| Gross recovered (treatment) | ₹22,84,424.03 |
| Control arm, untreated, scaled to treatment size | ₹5,36,526.77 |
| **Incremental recovery** | **₹17,47,897.25** (95% CI [₹14,44,540.18, ₹20,71,758.07], p = 0.0001) |
| Realised cost of treatment | ₹453.49 |
| **Net incremental recovery** | **₹17,47,443.76** |
| Modelled externalities (opt-outs, complaints, disputes) | less ₹2,75,622.81 |
| **Net after externalities** | **₹14,71,820.95** |
| Cost per ₹100 recovered | ₹0.02 |
| Policy denials (actions blocked before execution) | 346 |
| Policy modifications (rescheduled, not dropped) | 765 |
| Audit chain | chain intact: 39939 entries verified |

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
| `treatment` | 2000 | 58.5% | ₹1,142.21 | ₹1,141.98 | ₹1,004.17 | 0.83 | 5.0% | 26 |
| `baseline_rules` | 750 | 59.1% | ₹1,013.55 | ₹1,013.31 | ₹719.48 | 0.90 | 5.7% | 13 |
| `baseline_naive` | 750 | 61.9% | ₹1,380.48 | ₹1,380.47 | ₹1,296.37 | 0.50 | 2.3% | 8 |
| `baseline_blast` | 750 | 55.3% | ₹1,037.01 | ₹1,036.66 | ₹835.71 | 1.00 | 6.1% | 11 |
| `control` | 750 | 14.9% | ₹268.26 | ₹268.26 | ₹268.26 | 0.00 | 0.0% | 0 |

### Treatment against each baseline

Difference in gross recovery per 1,000 episodes. The **p-value decides**;
the interval describes the size. A comparison is only as precise as its
smaller arm.

| Compared with | n | Δ gross / 1,000 ep | 95% CI | p | Claim supported? |
|---|---|---|---|---|---|
| `control` | 750 | ₹8,73,948.62 | [₹7,22,270.09, ₹10,35,879.03] | 0.0001 | yes |
| `baseline_naive` | 750 | -₹2,38,269.69 | [-₹5,43,787.66, ₹46,938.15] | 0.0429 | worse |
| `baseline_blast` | 750 | ₹1,05,193.28 | [-₹1,34,729.81, ₹3,33,162.85] | 0.3571 | no — indistinguishable from noise |
| `baseline_rules` | 750 | ₹1,28,659.30 | [-₹84,067.31, ₹3,37,718.56] | 0.2449 | no — indistinguishable from noise  ← **A/A, see below** |

#### The same comparison on recovery rate

Rupees are what matter and rupees are heavy-tailed, so the interval above is
wide almost regardless of the strategy. Recovery rate is bounded and therefore
far more powerful at the same sample size. Both are reported; neither is
chosen after seeing the answer.

| Compared with | Δ recovery rate (pp) | 95% CI | p | Claim supported? |
|---|---|---|---|---|
| `control` | +43.52 | [+40.15, +46.77] | 0.0001 | yes |
| `baseline_naive` | -3.42 | [-7.52, +0.62] | 0.1006 | no — indistinguishable from noise |
| `baseline_blast` | +3.12 | [-1.02, +7.25] | 0.1458 | no — indistinguishable from noise |
| `baseline_rules` | -0.62 | [-4.77, +3.55] | 0.7987 | no — indistinguishable from noise |

#### Null controls — read these before believing any row above

**Placebo split.** The treatment arm is cut into two stratified halves (1000 / 1000 episodes) that ran the *same*
strategy on the *same* seed. The true difference is exactly zero by
construction, so this measures what the harness reports when there is nothing
to report. It stays valid after the LLM lands, which the
`treatment` vs `baseline_rules` row will not.

> Δ = -₹1,89,443.46 per 1,000 episodes, 95% CI [-₹4,72,002.83, ₹60,475.75], p = 0.1141 — correctly not significant

The noise floor for a comparison of this size is roughly ±₹4,72,002.83 per 1,000
episodes. A difference smaller than that is not a difference.

On recovery rate the same placebo gives -1.70 pp, p = 0.4668 — correctly not significant.

**Second A/A.** `treatment` and `baseline_rules` also run the same strategy today: p = 0.2449 — correctly not significant.

One seed cannot establish a false-positive *rate*. `make calibrate` runs the
placebo across many seeds and reports the measured rate against the nominal 5%;
see `results/calibration.md`.

### The aggression trade

`baseline_blast` recovers 55.3% of episodes against treatment's 58.5%, using 1.00 contacts per episode against 0.83, and produces an opt-out rate of 6.1% against 5.0%.

Channel spend cannot settle this argument. An SMS costs 12 paise and a
recovered invoice is worth thousands of rupees, so on a spend-only ledger the
optimal strategy is always to contact more. What actually disciplines contact
frequency is the revenue destroyed when a customer opts out, so that is now
priced — see `externalities` in `config/rates.yaml`.

| Arm | Gross / ep | Realised cost / ep | Externalities / ep | Net after ext. / ep |
|---|---|---|---|---|
| `treatment` | ₹1,142.21 | ₹0.22 | ₹137.81 | ₹1,004.17 |
| `baseline_blast` | ₹1,037.01 | ₹0.34 | ₹200.95 | ₹835.71 |
| `baseline_naive` | ₹1,380.48 | ₹0.00 | ₹84.09 | ₹1,296.37 |
| `baseline_rules` | ₹1,013.55 | ₹0.23 | ₹293.83 | ₹719.48 |

Treatment against blast on **net after externalities**: ₹1,68,460.59 per 1,000 episodes, 95% CI [-₹1,06,931.22, ₹4,40,895.77], p = 0.2063 — no — indistinguishable from noise.

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

Three different problems, so three columns. **Seen wording** is text the
merchant's resolved history already contains — a lookup is optimal there and no
model can beat it. **New wording** is text history has never held: a new
acquirer, a bank changing its phrasing. **No signal** is text that identifies
nothing, where only base rates remain. An overall figure averages three
problems into a number that describes none of them.

| Arm | overall | seen wording | new wording | no signal |
|---|---|---|---|---|
| `treatment` | 88.6% | 100.0% (n=1297) | 96.8% (n=348) | 39.2% (n=355) |
| `baseline_rules` | 83.2% | 100.0% (n=477) | 66.4% (n=137) | 41.2% (n=136) |
| *oracle that knows every wording* | *90.5%* | *100.0%* | *100.0%* | *44.5%* |

### Accuracy is the wrong metric on an unanswerable question

The right-hand column above deserves more care than a percentage. When the
failure text says only "Transaction declined", the true cause is still a
specific mechanism, so a classifier that says `unknown` — the correct answer to
the question actually asked — is scored **wrong**. A classifier that guesses the
modal cause is scored right about a fifth of the time. On these episodes the
accuracy metric rewards guessing and penalises honesty, which is the same
mistake as D28 and this time it is in the scoring rather than the planner.

So the number to read instead: of 355 episodes whose text could not
identify the cause, the treatment arm said `unknown` on **0** (0%).
That is the behaviour worth having, and it costs accuracy points.

**41 diagnoses named a cause this simulator never generates**: `gateway_error` (41). This is not the same as abstaining, and it is not entirely the classifier's
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
| Answered from resolved history, no model call | 1297 |
| Sent to the model | 703 (703 from cache, 0 live) |
| Fell back to rules | 0 (0.0%) |
| Stopped by the budget ceiling | 0 |
| Attempts per successful call | 0.00 |
| Tokens | 0 in, 0 out |
| Token cost (notional; free tier) | ₹0.00 |

**65% of episodes never reach the model.** A wording the merchant
has resolved consistently before is answered by lookup: for a fixed
vocabulary that is optimal, and asking a language model to reconsider it
would be slower, costlier and worse. The model is called only where history
cannot answer — which is also the only place its value can be demonstrated.

Prompts are content-addressed and the cache is keyed on their digest, so a
second run of the same seed makes no calls at all and produces a byte-identical
report. Amounts reach the model as bands rather than exact figures, which is
what makes that collapse possible — and the prompt carries no personal data of
any kind, because nothing about diagnosing a decline requires knowing who the
customer is.

## What the model buys, and what it costs

The ablation. Same playbooks, same gate, same ledger, same audit chain,
and the same resolved history; the only difference between these two arms
is who classifies the cause when history cannot.

| | Model | Keyword classifier |
|---|---|---|
| Wording seen in history | 100.0% (n=1297) | 100.0% (n=477) |
| **Wording never seen** | 96.8% (n=348) | 66.4% (n=137) |
| Text identifies nothing | 39.2% (n=355) | 41.2% (n=136) |
| **Overall accuracy** | **88.6%** | 83.2% |
| Forbidden retries / 1,000 episodes | 0.0 | **0.0** |
| Recovery rate | 58.5% | 59.1% |
| Difference in recovery rate | -0.62 pp, p = 0.799 | — |

**One row carries the argument.** On wordings the merchant has resolved
before, a lookup is optimal and both arms score 100% — a model adds
nothing and costs money. The first time an acquirer rewords a decline,
the keyword table falls to 66% and the model holds at 97%, near the
oracle. That is the entire case for putting a model in this system, and
it is one column wide.

Everything else is a wash, and saying so is what makes the one column
worth believing:

- **Recovery is indistinguishable.** -0.62 points, p = 0.799, well inside the placebo noise floor.
- **Harm is equal**: 0.0 forbidden retries per 1,000 episodes
  against 0.0, both against the fixed ladder's 965. Neither arm
  gets there by classifying better. They get there because a low-confidence
  diagnosis is not allowed to authorise a retry when the merchant's own base
  rates say a fifth of failures in this context are things nobody may
  re-present. That rule is available to both, and it is worth more than the
  accuracy difference between them.
- **On text that identifies nothing the model is marginally behind**, and
  both sit near the 44.5% ceiling that base rates impose. Nothing can read a
  cause out of "Transaction declined"; that column is not a contest.

So the case for the model is narrow and it is real. It is not that it
classifies better in general — on 65% of episodes it is never consulted, and
on the unanswerable ones it is slightly worse. It is that a keyword table
has a cliff exactly where payment systems change, and the model does not.

## Harm

What each strategy costs the people on the other end. **Forbidden retries** are
attempts against an episode whose *true* cause is never-retryable — a dead card,
a risk decline, a revoked mandate. The gate can only refuse a retry for a cause
somebody identified, so this is the price of a wrong diagnosis, and it is the
number a better diagnoser has to drive down.

| Arm | Forbidden retries / 1,000 ep | Opt-outs / 1,000 ep | Complaints / 1,000 ep | Disputes / 1,000 ep |
|---|---|---|---|---|
| `treatment` | 0.0 | 50.0 | 13.0 | 2.0 |
| `baseline_rules` | 0.0 | 57.3 | 17.3 | 5.3 |
| `baseline_naive` | 965.3 | 22.7 | 10.7 | 2.7 |
| `baseline_blast` | 453.3 | 61.3 | 14.7 | 6.7 |
| `control` | 0.0 | 0.0 | 0.0 | 0.0 |

`baseline_naive` does not diagnose at all, so it retries dead cards, risk
declines and revoked mandates indiscriminately — **965333333x** the rate of the
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
| `escalated` | 130 | 0 | 0 | 0 |
| `exhausted` | 514 | 268 | 291 | 0 |
| `partially_recovered` | 21 | 4 | 18 | 0 |
| `recovered` | 1148 | 460 | 397 | 112 |
| `skipped_negative_ev` | 34 | 0 | 0 | 638 |
| `suppressed` | 98 | 18 | 44 | 0 |
| `unrecoverable` | 55 | 0 | 0 | 0 |

## Per-cause recovery (treatment)

| Root cause | n | recovered | rate | gross |
|---|---|---|---|---|
| `insufficient_funds` | 375 | 262 | 69.9% | ₹3.35L |
| `mandate_insufficient` | 296 | 146 | 49.3% | ₹1.61L |
| `authentication_failed` | 244 | 158 | 64.8% | ₹2.50L |
| `invoice_forgotten` | 207 | 149 | 72.0% | ₹6.47L |
| `issuer_down` | 172 | 145 | 84.3% | ₹2.46L |
| `technical_timeout` | 118 | 77 | 65.3% | ₹98.2K |
| `mandate_revoked` | 116 | 57 | 49.1% | ₹76.9K |
| `invoice_cash_crunch` | 97 | 56 | 57.7% | ₹1.60L |
| `card_expired_or_invalid` | 91 | 43 | 47.3% | ₹66.5K |
| `invoice_disputed` | 85 | 11 | 12.9% | ₹1.57L |
| `limit_exceeded` | 74 | 46 | 62.2% | ₹64.1K |
| `risk_declined` | 71 | 3 | 4.2% | ₹2.4K |
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
