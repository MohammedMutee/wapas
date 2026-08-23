# Wapas — evaluation report

Seed `20260901` · episodes `5000` · policy `contact/3+money/4+escalation/3` · rates `rates/1` · sim `sim/3`

> **In-simulation results.** Every number below is produced by the synthetic
> world defined in `sim/params.yaml`, whose generative parameters are published
> and which the agent never reads. These are not measured Razorpay statistics.

> **Note on the current treatment arm.** The treatment arm currently runs the **rules-only** planner. The LLM agent is not yet wired in, so treatment and `baseline_rules` are the same policy differing only by sample. Any gap between them is sampling noise, and the LLM ablation is not yet meaningful.

## Headline

| Metric | Value |
|---|---|
| Total revenue at risk | ₹1,16,43,570.53 |
| Gross recovered (treatment) | ₹22,67,035.36 |
| Control arm, untreated, scaled to treatment size | ₹5,36,526.77 |
| **Incremental recovery** | **₹17,30,508.58** (95% CI [₹14,27,696.03, ₹20,50,796.35], p = 0.0001) |
| Realised cost of treatment | ₹391.75 |
| **Net incremental recovery** | **₹17,30,116.83** |
| Modelled externalities (opt-outs, complaints, disputes) | less ₹2,89,005.98 |
| **Net after externalities** | **₹14,41,110.85** |
| Cost per ₹100 recovered | ₹0.02 |
| Policy denials (actions blocked before execution) | 21 |
| Policy modifications (rescheduled, not dropped) | 764 |
| Audit chain | chain intact: 39533 entries verified |

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
| `treatment` | 2000 | 56.6% | ₹1,133.51 | ₹1,133.32 | ₹988.81 | 0.83 | 5.3% | 26 |
| `baseline_rules` | 750 | 58.9% | ₹1,015.03 | ₹1,014.83 | ₹711.96 | 0.85 | 5.7% | 12 |
| `baseline_naive` | 750 | 61.9% | ₹1,380.48 | ₹1,380.47 | ₹1,296.37 | 0.50 | 2.3% | 8 |
| `baseline_blast` | 750 | 55.3% | ₹1,037.01 | ₹1,036.66 | ₹835.71 | 1.00 | 6.1% | 11 |
| `control` | 750 | 14.9% | ₹268.26 | ₹268.26 | ₹268.26 | 0.00 | 0.0% | 0 |

### Treatment against each baseline

Difference in gross recovery per 1,000 episodes. The **p-value decides**;
the interval describes the size. A comparison is only as precise as its
smaller arm.

| Compared with | n | Δ gross / 1,000 ep | 95% CI | p | Claim supported? |
|---|---|---|---|---|---|
| `control` | 750 | ₹8,65,254.29 | [₹7,13,848.01, ₹10,25,398.17] | 0.0001 | yes |
| `baseline_naive` | 750 | -₹2,46,964.02 | [-₹5,55,026.83, ₹38,058.88] | 0.0379 | worse |
| `baseline_blast` | 750 | ₹96,498.94 | [-₹1,42,690.26, ₹3,24,059.26] | 0.3992 | no — indistinguishable from noise |
| `baseline_rules` | 750 | ₹1,18,486.57 | [-₹92,224.01, ₹3,25,888.61] | 0.2868 | no — indistinguishable from noise  ← **A/A, see below** |

#### The same comparison on recovery rate

Rupees are what matter and rupees are heavy-tailed, so the interval above is
wide almost regardless of the strategy. Recovery rate is bounded and therefore
far more powerful at the same sample size. Both are reported; neither is
chosen after seeing the answer.

| Compared with | Δ recovery rate (pp) | 95% CI | p | Claim supported? |
|---|---|---|---|---|
| `control` | +41.72 | [+38.38, +45.00] | 0.0001 | yes |
| `baseline_naive` | -5.22 | [-9.32, -1.17] | 0.0133 | worse |
| `baseline_blast` | +1.32 | [-2.80, +5.48] | 0.5431 | no — indistinguishable from noise |
| `baseline_rules` | -2.28 | [-6.45, +1.82] | 0.2952 | no — indistinguishable from noise |

#### Null controls — read these before believing any row above

**Placebo split.** The treatment arm is cut into two stratified halves (1000 / 1000 episodes) that ran the *same*
strategy on the *same* seed. The true difference is exactly zero by
construction, so this measures what the harness reports when there is nothing
to report. It stays valid after the LLM lands, which the
`treatment` vs `baseline_rules` row will not.

> Δ = -₹1,68,731.87 per 1,000 episodes, 95% CI [-₹4,56,460.09, ₹82,927.43], p = 0.1687 — correctly not significant

The noise floor for a comparison of this size is roughly ±₹4,56,460.09 per 1,000
episodes. A difference smaller than that is not a difference.

On recovery rate the same placebo gives -1.10 pp, p = 0.6584 — correctly not significant.

**Second A/A.** `treatment` and `baseline_rules` also run the same strategy today: p = 0.2868 — correctly not significant.

One seed cannot establish a false-positive *rate*. `make calibrate` runs the
placebo across many seeds and reports the measured rate against the nominal 5%;
see `results/calibration.md`.

### The aggression trade

`baseline_blast` recovers 55.3% of episodes against treatment's 56.6%, using 1.00 contacts per episode against 0.83, and produces an opt-out rate of 6.1% against 5.3%.

Channel spend cannot settle this argument. An SMS costs 12 paise and a
recovered invoice is worth thousands of rupees, so on a spend-only ledger the
optimal strategy is always to contact more. What actually disciplines contact
frequency is the revenue destroyed when a customer opts out, so that is now
priced — see `externalities` in `config/rates.yaml`.

| Arm | Gross / ep | Realised cost / ep | Externalities / ep | Net after ext. / ep |
|---|---|---|---|---|
| `treatment` | ₹1,133.51 | ₹0.19 | ₹144.50 | ₹988.81 |
| `baseline_blast` | ₹1,037.01 | ₹0.34 | ₹200.95 | ₹835.71 |
| `baseline_naive` | ₹1,380.48 | ₹0.00 | ₹84.09 | ₹1,296.37 |
| `baseline_rules` | ₹1,015.03 | ₹0.19 | ₹302.87 | ₹711.96 |

Treatment against blast on **net after externalities**: ₹1,53,105.54 per 1,000 episodes, 95% CI [-₹1,22,683.45, ₹4,22,465.09], p = 0.2549 — no — indistinguishable from noise.

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

| Arm | classified | correct | accuracy |
|---|---|---|---|
| `treatment` | 2000 | 1489 | 74.5% |
| `baseline_rules` | 750 | 560 | 74.7% |

## Harm

What each strategy costs the people on the other end. **Forbidden retries** are
attempts against an episode whose *true* cause is never-retryable — a dead card,
a risk decline, a revoked mandate. The gate can only refuse a retry for a cause
somebody identified, so this is the price of a wrong diagnosis, and it is the
number a better diagnoser has to drive down.

| Arm | Forbidden retries / 1,000 ep | Opt-outs / 1,000 ep | Complaints / 1,000 ep | Disputes / 1,000 ep |
|---|---|---|---|---|
| `treatment` | 21.0 | 53.5 | 13.0 | 2.0 |
| `baseline_rules` | 21.3 | 57.3 | 16.0 | 5.3 |
| `baseline_naive` | 965.3 | 22.7 | 10.7 | 2.7 |
| `baseline_blast` | 453.3 | 61.3 | 14.7 | 6.7 |
| `control` | 0.0 | 0.0 | 0.0 | 0.0 |

`baseline_naive` does not diagnose at all, so it retries dead cards, risk
declines and revoked mandates indiscriminately — **46x** the rate of the
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
| `exhausted` | 410 | 268 | 291 | 0 |
| `partially_recovered` | 21 | 4 | 18 | 0 |
| `recovered` | 1112 | 460 | 397 | 112 |
| `skipped_negative_ev` | 0 | 0 | 0 | 638 |
| `suppressed` | 102 | 18 | 44 | 0 |
| `unrecoverable` | 225 | 0 | 0 | 0 |

## Per-cause recovery (treatment)

| Root cause | n | recovered | rate | gross |
|---|---|---|---|---|
| `insufficient_funds` | 375 | 218 | 58.1% | ₹3.01L |
| `mandate_insufficient` | 296 | 162 | 54.7% | ₹1.81L |
| `authentication_failed` | 244 | 147 | 60.2% | ₹2.35L |
| `invoice_forgotten` | 207 | 149 | 72.0% | ₹6.47L |
| `issuer_down` | 172 | 148 | 86.0% | ₹2.55L |
| `technical_timeout` | 118 | 75 | 63.6% | ₹88.5K |
| `mandate_revoked` | 116 | 58 | 50.0% | ₹78.3K |
| `invoice_cash_crunch` | 97 | 56 | 57.7% | ₹1.60L |
| `card_expired_or_invalid` | 91 | 42 | 46.2% | ₹66.2K |
| `invoice_disputed` | 85 | 11 | 12.9% | ₹1.57L |
| `limit_exceeded` | 74 | 43 | 58.1% | ₹59.3K |
| `risk_declined` | 71 | 4 | 5.6% | ₹12.5K |
| `customer_cancelled` | 54 | 20 | 37.0% | ₹26.1K |

## Known weaknesses

- Results are in-simulation. The sensitivity sweep (±30% on every parameter)
  is not yet implemented, so these numbers are one point in parameter space.
- The treatment arm currently runs the **rules-only** planner. The LLM agent is not yet wired in, so treatment and `baseline_rules` are the same policy differing only by sample. Any gap between them is sampling noise, and the LLM ablation is not yet meaningful.
- Self-recovery is credited to whichever arm the episode fell in, including
  treatment. That is correct — it is exactly what the control arm subtracts —
  but it means the gross figure above is *not* the agent's achievement.
- Externality pricing is a model, not a measurement. See the aggression trade.
- Realised costs cover channel spend only. LLM token cost joins the ledger
  when the agent lands; free-tier models are priced notionally (see
  `config/rates.yaml`).

Reproduce: `make eval SEED=20260901`
