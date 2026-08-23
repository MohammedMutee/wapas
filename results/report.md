# Wapas — evaluation report

Seed `20260901` · episodes `5000` · policy `contact/3+money/4+escalation/3` · rates `rates/1` · sim `sim/2`

> **In-simulation results.** Every number below is produced by the synthetic
> world defined in `sim/params.yaml`, whose generative parameters are published
> and which the agent never reads. These are not measured Razorpay statistics.

> **Note on the current treatment arm.** The treatment arm currently runs the **rules-only** planner. The LLM agent is not yet wired in, so treatment and `baseline_rules` are the same policy differing only by sample. Any gap between them is sampling noise, and the LLM ablation is not yet meaningful.

## Headline

| Metric | Value |
|---|---|
| Total revenue at risk | ₹1,16,43,570.53 |
| Gross recovered (treatment) | ₹24,47,524.79 |
| Control arm, untreated, scaled to treatment size | ₹5,36,526.77 |
| **Incremental recovery** | **₹19,10,998.01** (95% CI [₹16,01,852.51, ₹22,35,764.00], p = 0.0001) |
| Realised cost of treatment | ₹432.27 |
| **Net incremental recovery** | **₹19,10,565.74** |
| Modelled externalities (opt-outs, complaints, disputes) | less ₹2,69,763.46 |
| **Net after externalities** | **₹16,40,802.28** |
| Cost per ₹100 recovered | ₹0.02 |
| Policy denials (actions blocked before execution) | 26 |
| Policy modifications (rescheduled, not dropped) | 731 |
| Audit chain | chain intact: 38827 entries verified |

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

## Arms

Arms differ in size, so **totals are not comparable** — the per-episode
columns are the ones to read.

| Arm | n | Recovery rate | Gross / ep | Net / ep | Net after ext. / ep | Contacts / ep | Opt-out rate | Complaints |
|---|---|---|---|---|---|---|---|---|
| `treatment` | 2000 | 62.8% | ₹1,223.76 | ₹1,223.54 | ₹1,088.66 | 0.78 | 5.0% | 24 |
| `baseline_rules` | 750 | 64.8% | ₹1,076.89 | ₹1,076.68 | ₹815.58 | 0.79 | 5.5% | 9 |
| `baseline_naive` | 750 | 61.9% | ₹1,380.48 | ₹1,380.47 | ₹1,315.68 | 0.50 | 2.3% | 8 |
| `baseline_blast` | 750 | 55.3% | ₹1,037.01 | ₹1,036.66 | ₹844.77 | 1.00 | 6.1% | 11 |
| `control` | 750 | 14.9% | ₹268.26 | ₹268.26 | ₹268.26 | 0.00 | 0.0% | 0 |

### Treatment against each baseline

Difference in gross recovery per 1,000 episodes. The **p-value decides**;
the interval describes the size. A comparison is only as precise as its
smaller arm.

| Compared with | n | Δ gross / 1,000 ep | 95% CI | p | Claim supported? |
|---|---|---|---|---|---|
| `control` | 750 | ₹9,55,499.00 | [₹8,00,926.25, ₹11,17,882.00] | 0.0001 | yes |
| `baseline_naive` | 750 | -₹1,56,719.31 | [-₹4,65,255.96, ₹1,28,593.28] | 0.1958 | no — indistinguishable from noise |
| `baseline_blast` | 750 | ₹1,86,743.66 | [-₹54,911.04, ₹4,17,068.29] | 0.0924 | no — indistinguishable from noise |
| `baseline_rules` | 750 | ₹1,46,863.58 | [-₹68,402.47, ₹3,53,652.45] | 0.1814 | no — indistinguishable from noise  ← **A/A, see below** |

#### The same comparison on recovery rate

Rupees are what matter and rupees are heavy-tailed, so the interval above is
wide almost regardless of the strategy. Recovery rate is bounded and therefore
far more powerful at the same sample size. Both are reported; neither is
chosen after seeing the answer.

| Compared with | Δ recovery rate (pp) | 95% CI | p | Claim supported? |
|---|---|---|---|---|
| `control` | +47.92 | [+44.57, +51.12] | 0.0001 | yes |
| `baseline_naive` | +0.98 | [-3.10, +4.97] | 0.6575 | no — indistinguishable from noise |
| `baseline_blast` | +7.52 | [+3.38, +11.58] | 0.0003 | yes |
| `baseline_rules` | -1.95 | [-6.00, +2.08] | 0.3459 | no — indistinguishable from noise |

#### Null controls — read these before believing any row above

**Placebo split.** The treatment arm is cut into two stratified halves (1000 / 1000 episodes) that ran the *same*
strategy on the *same* seed. The true difference is exactly zero by
construction, so this measures what the harness reports when there is nothing
to report. It stays valid after the LLM lands, which the
`treatment` vs `baseline_rules` row will not.

> Δ = -₹2,06,095.22 per 1,000 episodes, 95% CI [-₹4,93,685.93, ₹53,135.83], p = 0.0855 — correctly not significant

The noise floor for a comparison of this size is roughly ±₹4,93,685.93 per 1,000
episodes. A difference smaller than that is not a difference.

On recovery rate the same placebo gives -3.70 pp, p = 0.0957 — correctly not significant.

**Second A/A.** `treatment` and `baseline_rules` also run the same strategy today: p = 0.1814 — correctly not significant.

One seed cannot establish a false-positive *rate*. `make calibrate` runs the
placebo across many seeds and reports the measured rate against the nominal 5%;
see `results/calibration.md`.

### The aggression trade

`baseline_blast` recovers 55.3% of episodes against treatment's 62.8%, using 1.00 contacts per episode against 0.78, and produces an opt-out rate of 6.1% against 5.0%.

Channel spend cannot settle this argument. An SMS costs 12 paise and a
recovered invoice is worth thousands of rupees, so on a spend-only ledger the
optimal strategy is always to contact more. What actually disciplines contact
frequency is the revenue destroyed when a customer opts out, so that is now
priced — see `externalities` in `config/rates.yaml`.

| Arm | Gross / ep | Realised cost / ep | Externalities / ep | Net after ext. / ep |
|---|---|---|---|---|
| `treatment` | ₹1,223.76 | ₹0.21 | ₹134.88 | ₹1,088.66 |
| `baseline_blast` | ₹1,037.01 | ₹0.34 | ₹191.88 | ₹844.77 |
| `baseline_naive` | ₹1,380.48 | ₹0.00 | ₹64.79 | ₹1,315.68 |
| `baseline_rules` | ₹1,076.89 | ₹0.21 | ₹261.10 | ₹815.58 |

Treatment against blast on **net after externalities**: ₹2,43,884.59 per 1,000 episodes, 95% CI [-₹31,487.24, ₹5,11,221.76], p = 0.0629 — no — indistinguishable from noise.

**The externality figures are assumptions, not measurements**, and they are
the most contestable numbers in this project. They are reported on their own
line, and the net-before-externalities column is kept, so a reader who
rejects the model can still read every other result. The sensitivity sweep
varies them by ±30% like everything else.

## Diagnosis accuracy

| Arm | classified | correct | accuracy |
|---|---|---|---|
| `treatment` | 2000 | 1704 | 85.2% |
| `baseline_rules` | 750 | 647 | 86.3% |

## Terminal states — the stopping rules, exercised

| State | Treatment | Naive | Blast | Control |
|---|---|---|---|---|
| `escalated` | 130 | 0 | 0 | 0 |
| `exhausted` | 449 | 268 | 291 | 0 |
| `partially_recovered` | 21 | 4 | 18 | 0 |
| `recovered` | 1236 | 460 | 397 | 112 |
| `skipped_negative_ev` | 0 | 0 | 0 | 638 |
| `suppressed` | 93 | 18 | 44 | 0 |
| `unrecoverable` | 71 | 0 | 0 | 0 |

## Per-cause recovery (treatment)

| Root cause | n | recovered | rate | gross |
|---|---|---|---|---|
| `insufficient_funds` | 375 | 276 | 73.6% | ₹3.87L |
| `mandate_insufficient` | 296 | 179 | 60.5% | ₹1.96L |
| `authentication_failed` | 244 | 161 | 66.0% | ₹2.55L |
| `invoice_forgotten` | 207 | 149 | 72.0% | ₹6.47L |
| `issuer_down` | 172 | 170 | 98.8% | ₹2.92L |
| `technical_timeout` | 118 | 79 | 66.9% | ₹89.3K |
| `mandate_revoked` | 116 | 66 | 56.9% | ₹95.7K |
| `invoice_cash_crunch` | 97 | 63 | 64.9% | ₹1.83L |
| `card_expired_or_invalid` | 91 | 44 | 48.4% | ₹66.6K |
| `invoice_disputed` | 85 | 10 | 11.8% | ₹1.55L |
| `limit_exceeded` | 74 | 44 | 59.5% | ₹60.6K |
| `risk_declined` | 71 | 0 | 0.0% | ₹0 |
| `customer_cancelled` | 54 | 16 | 29.6% | ₹19.3K |

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
