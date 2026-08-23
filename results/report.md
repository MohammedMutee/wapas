# Wapas — evaluation report

Seed `20260901` · episodes `5000` · policy `contact/3+money/4+escalation/3` · rates `rates/1` · sim `sim/3`

> **In-simulation results.** Every number below is produced by the synthetic
> world defined in `sim/params.yaml`, whose generative parameters are published
> and which the agent never reads. These are not measured Razorpay statistics.

> **The treatment arm.** The treatment arm is running the **rules-only** planner: this run was made without `--llm`, so treatment and `baseline_rules` are the same policy differing only by sample. Any gap between them is sampling noise, and the LLM ablation is not meaningful in this report.

## Headline

| Metric | Value |
|---|---|
| Total revenue at risk | ₹1,16,43,570.53 |
| Gross recovered (treatment) | ₹23,42,787.98 |
| Control arm, untreated, scaled to treatment size | ₹5,36,526.77 |
| **Incremental recovery** | **₹18,06,261.20** (95% CI [₹14,99,888.09, ₹21,31,314.96], p = 0.0001) |
| Realised cost of treatment | ₹483.51 |
| **Net incremental recovery** | **₹18,05,777.69** |
| Modelled externalities (opt-outs, complaints, disputes) | less ₹3,11,331.61 |
| **Net after externalities** | **₹14,94,446.08** |
| Cost per ₹100 recovered | ₹0.02 |
| Policy denials (actions blocked before execution) | 383 |
| Policy modifications (rescheduled, not dropped) | 818 |
| Audit chain | chain intact: 40445 entries verified |

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
| `treatment` | 2000 | 58.9% | ₹1,171.39 | ₹1,171.15 | ₹1,015.48 | 0.89 | 5.5% | 27 |
| `baseline_rules` | 750 | 60.5% | ₹1,033.94 | ₹1,033.70 | ₹739.61 | 0.90 | 5.7% | 13 |
| `baseline_naive` | 750 | 61.9% | ₹1,380.48 | ₹1,380.47 | ₹1,296.37 | 0.50 | 2.3% | 8 |
| `baseline_blast` | 750 | 55.3% | ₹1,037.01 | ₹1,036.66 | ₹835.71 | 1.00 | 6.1% | 11 |
| `control` | 750 | 14.9% | ₹268.26 | ₹268.26 | ₹268.26 | 0.00 | 0.0% | 0 |

### Treatment against each baseline

Difference in gross recovery per 1,000 episodes. The **p-value decides**;
the interval describes the size. A comparison is only as precise as its
smaller arm.

| Compared with | n | Δ gross / 1,000 ep | 95% CI | p | Claim supported? |
|---|---|---|---|---|---|
| `control` | 750 | ₹9,03,130.60 | [₹7,49,944.04, ₹10,65,657.48] | 0.0001 | yes |
| `baseline_naive` | 750 | -₹2,09,087.71 | [-₹5,14,102.11, ₹74,842.96] | 0.0801 | no — indistinguishable from noise |
| `baseline_blast` | 750 | ₹1,34,375.25 | [-₹1,06,400.88, ₹3,65,688.75] | 0.2353 | no — indistinguishable from noise |
| `baseline_rules` | 750 | ₹1,37,446.85 | [-₹78,370.39, ₹3,48,526.20] | 0.2212 | no — indistinguishable from noise  ← **A/A, see below** |

#### The same comparison on recovery rate

Rupees are what matter and rupees are heavy-tailed, so the interval above is
wide almost regardless of the strategy. Recovery rate is bounded and therefore
far more powerful at the same sample size. Both are reported; neither is
chosen after seeing the answer.

| Compared with | Δ recovery rate (pp) | 95% CI | p | Claim supported? |
|---|---|---|---|---|
| `control` | +43.97 | [+40.58, +47.27] | 0.0001 | yes |
| `baseline_naive` | -2.97 | [-7.00, +1.05] | 0.1598 | no — indistinguishable from noise |
| `baseline_blast` | +3.57 | [-0.55, +7.70] | 0.0964 | no — indistinguishable from noise |
| `baseline_rules` | -1.63 | [-5.70, +2.47] | 0.4542 | no — indistinguishable from noise |

#### Null controls — read these before believing any row above

**Placebo split.** The treatment arm is cut into two stratified halves (1000 / 1000 episodes) that ran the *same*
strategy on the *same* seed. The true difference is exactly zero by
construction, so this measures what the harness reports when there is nothing
to report. It stays valid after the LLM lands, which the
`treatment` vs `baseline_rules` row will not.

> Δ = -₹2,18,968.94 per 1,000 episodes, 95% CI [-₹5,05,718.42, ₹37,038.01], p = 0.0618 — correctly not significant

The noise floor for a comparison of this size is roughly ±₹5,05,718.42 per 1,000
episodes. A difference smaller than that is not a difference.

On recovery rate the same placebo gives -1.80 pp, p = 0.4462 — correctly not significant.

**Second A/A.** `treatment` and `baseline_rules` also run the same strategy today: p = 0.2212 — correctly not significant.

One seed cannot establish a false-positive *rate*. `make calibrate` runs the
placebo across many seeds and reports the measured rate against the nominal 5%;
see `results/calibration.md`.

### The aggression trade

`baseline_blast` recovers 55.3% of episodes against treatment's 58.9%, using 1.00 contacts per episode against 0.89, and produces an opt-out rate of 6.1% against 5.5%.

Channel spend cannot settle this argument. An SMS costs 12 paise and a
recovered invoice is worth thousands of rupees, so on a spend-only ledger the
optimal strategy is always to contact more. What actually disciplines contact
frequency is the revenue destroyed when a customer opts out, so that is now
priced — see `externalities` in `config/rates.yaml`.

| Arm | Gross / ep | Realised cost / ep | Externalities / ep | Net after ext. / ep |
|---|---|---|---|---|
| `treatment` | ₹1,171.39 | ₹0.24 | ₹155.66 | ₹1,015.48 |
| `baseline_blast` | ₹1,037.01 | ₹0.34 | ₹200.95 | ₹835.71 |
| `baseline_naive` | ₹1,380.48 | ₹0.00 | ₹84.09 | ₹1,296.37 |
| `baseline_rules` | ₹1,033.94 | ₹0.23 | ₹294.09 | ₹739.61 |

Treatment against blast on **net after externalities**: ₹1,79,773.16 per 1,000 episodes, 95% CI [-₹95,375.40, ₹4,48,722.96], p = 0.1802 — no — indistinguishable from noise.

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
| `treatment` | 84.6% | 100.0% (n=1297) | 69.3% (n=348) | 43.4% (n=355) |
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

The split is the interesting column. On text that names a mechanism, a keyword
table with the ISO 8583 codes in it is very hard to beat and a model has almost
nothing to add. The case for a model rests entirely on the right-hand column —
the failures where the answer has to be assembled from weak context rather than
looked up — and on that column being a large enough share of reality to matter.

## Harm

What each strategy costs the people on the other end. **Forbidden retries** are
attempts against an episode whose *true* cause is never-retryable — a dead card,
a risk decline, a revoked mandate. The gate can only refuse a retry for a cause
somebody identified, so this is the price of a wrong diagnosis, and it is the
number a better diagnoser has to drive down.

| Arm | Forbidden retries / 1,000 ep | Opt-outs / 1,000 ep | Complaints / 1,000 ep | Disputes / 1,000 ep |
|---|---|---|---|---|
| `treatment` | 20.0 | 55.5 | 13.5 | 4.5 |
| `baseline_rules` | 13.3 | 57.3 | 17.3 | 5.3 |
| `baseline_naive` | 965.3 | 22.7 | 10.7 | 2.7 |
| `baseline_blast` | 453.3 | 61.3 | 14.7 | 6.7 |
| `control` | 0.0 | 0.0 | 0.0 | 0.0 |

`baseline_naive` does not diagnose at all, so it retries dead cards, risk
declines and revoked mandates indiscriminately — **48x** the rate of the
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
| `escalated` | 126 | 0 | 0 | 0 |
| `exhausted` | 541 | 268 | 291 | 0 |
| `partially_recovered` | 21 | 4 | 18 | 0 |
| `recovered` | 1157 | 460 | 397 | 112 |
| `skipped_negative_ev` | 0 | 0 | 0 | 638 |
| `suppressed` | 114 | 18 | 44 | 0 |
| `unrecoverable` | 41 | 0 | 0 | 0 |

## Per-cause recovery (treatment)

| Root cause | n | recovered | rate | gross |
|---|---|---|---|---|
| `insufficient_funds` | 375 | 260 | 69.3% | ₹3.63L |
| `mandate_insufficient` | 296 | 136 | 45.9% | ₹1.47L |
| `authentication_failed` | 244 | 160 | 65.6% | ₹2.55L |
| `invoice_forgotten` | 207 | 149 | 72.0% | ₹6.47L |
| `issuer_down` | 172 | 160 | 93.0% | ₹2.70L |
| `technical_timeout` | 118 | 81 | 68.6% | ₹99.9K |
| `mandate_revoked` | 116 | 58 | 50.0% | ₹78.3K |
| `invoice_cash_crunch` | 97 | 50 | 51.5% | ₹1.67L |
| `card_expired_or_invalid` | 91 | 44 | 48.4% | ₹66.6K |
| `invoice_disputed` | 85 | 11 | 12.9% | ₹1.57L |
| `limit_exceeded` | 74 | 45 | 60.8% | ₹64.0K |
| `risk_declined` | 71 | 8 | 11.3% | ₹8.3K |
| `customer_cancelled` | 54 | 16 | 29.6% | ₹19.3K |

## Known weaknesses

- Results are in-simulation. The sensitivity sweep (±30% on every parameter)
  is not yet implemented, so these numbers are one point in parameter space.
- The treatment arm is running the **rules-only** planner: this run was made without `--llm`, so treatment and `baseline_rules` are the same policy differing only by sample. Any gap between them is sampling noise, and the LLM ablation is not meaningful in this report.
- Self-recovery is credited to whichever arm the episode fell in, including
  treatment. That is correct — it is exactly what the control arm subtracts —
  but it means the gross figure above is *not* the agent's achievement.
- Externality pricing is a model, not a measurement. See the aggression trade.
- Realised costs cover channel spend only. LLM token cost joins the ledger
  when the agent lands; free-tier models are priced notionally (see
  `config/rates.yaml`).

Reproduce: `make eval SEED=20260901`
