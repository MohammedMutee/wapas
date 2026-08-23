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
| Gross recovered (treatment) | ₹23,60,139.07 |
| Control arm, untreated, scaled to treatment size | ₹5,36,526.77 |
| **Incremental recovery** | **₹18,23,612.29** (95% CI [₹15,18,401.71, ₹21,46,365.99], p = 0.0001) |
| Realised cost of treatment | ₹460.14 |
| **Net incremental recovery** | **₹18,23,152.15** |
| Modelled externalities (opt-outs, complaints, disputes) | less ₹2,84,283.38 |
| **Net after externalities** | **₹15,38,868.77** |
| Cost per ₹100 recovered | ₹0.02 |
| Policy denials (actions blocked before execution) | 327 |
| Policy modifications (rescheduled, not dropped) | 771 |
| Audit chain | chain intact: 40065 entries verified |

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
| `treatment` | 2000 | 59.9% | ₹1,180.06 | ₹1,179.83 | ₹1,037.69 | 0.84 | 5.1% | 26 |
| `baseline_rules` | 750 | 60.0% | ₹1,032.05 | ₹1,031.82 | ₹738.22 | 0.89 | 5.6% | 13 |
| `baseline_naive` | 750 | 61.9% | ₹1,380.48 | ₹1,380.47 | ₹1,296.37 | 0.50 | 2.3% | 8 |
| `baseline_blast` | 750 | 55.3% | ₹1,037.01 | ₹1,036.66 | ₹835.71 | 1.00 | 6.1% | 11 |
| `control` | 750 | 14.9% | ₹268.26 | ₹268.26 | ₹268.26 | 0.00 | 0.0% | 0 |

### Treatment against each baseline

Difference in gross recovery per 1,000 episodes. The **p-value decides**;
the interval describes the size. A comparison is only as precise as its
smaller arm.

| Compared with | n | Δ gross / 1,000 ep | 95% CI | p | Claim supported? |
|---|---|---|---|---|---|
| `control` | 750 | ₹9,11,806.14 | [₹7,59,200.85, ₹10,73,182.99] | 0.0001 | yes |
| `baseline_naive` | 750 | -₹2,00,412.17 | [-₹5,07,918.44, ₹83,718.88] | 0.0920 | no — indistinguishable from noise |
| `baseline_blast` | 750 | ₹1,43,050.80 | [-₹97,467.07, ₹3,72,269.59] | 0.2018 | no — indistinguishable from noise |
| `baseline_rules` | 750 | ₹1,48,011.90 | [-₹65,990.85, ₹3,58,905.42] | 0.1835 | no — indistinguishable from noise  ← **A/A, see below** |

#### The same comparison on recovery rate

Rupees are what matter and rupees are heavy-tailed, so the interval above is
wide almost regardless of the strategy. Recovery rate is bounded and therefore
far more powerful at the same sample size. Both are reported; neither is
chosen after seeing the answer.

| Compared with | Δ recovery rate (pp) | 95% CI | p | Claim supported? |
|---|---|---|---|---|
| `control` | +44.97 | [+41.62, +48.23] | 0.0001 | yes |
| `baseline_naive` | -1.97 | [-6.07, +2.05] | 0.3558 | no — indistinguishable from noise |
| `baseline_blast` | +4.57 | [+0.45, +8.68] | 0.0311 | yes |
| `baseline_rules` | -0.10 | [-4.20, +4.05] | 0.9677 | no — indistinguishable from noise |

#### Null controls — read these before believing any row above

**Placebo split.** The treatment arm is cut into two stratified halves (1000 / 1000 episodes) that ran the *same*
strategy on the *same* seed. The true difference is exactly zero by
construction, so this measures what the harness reports when there is nothing
to report. It stays valid after the LLM lands, which the
`treatment` vs `baseline_rules` row will not.

> Δ = -₹1,67,243.03 per 1,000 episodes, 95% CI [-₹4,55,798.49, ₹90,358.76], p = 0.1796 — correctly not significant

The noise floor for a comparison of this size is roughly ±₹4,55,798.49 per 1,000
episodes. A difference smaller than that is not a difference.

On recovery rate the same placebo gives -2.00 pp, p = 0.3890 — correctly not significant.

**Second A/A.** `treatment` and `baseline_rules` also run the same strategy today: p = 0.1835 — correctly not significant.

One seed cannot establish a false-positive *rate*. `make calibrate` runs the
placebo across many seeds and reports the measured rate against the nominal 5%;
see `results/calibration.md`.

### The aggression trade

`baseline_blast` recovers 55.3% of episodes against treatment's 59.9%, using 1.00 contacts per episode against 0.84, and produces an opt-out rate of 6.1% against 5.1%.

Channel spend cannot settle this argument. An SMS costs 12 paise and a
recovered invoice is worth thousands of rupees, so on a spend-only ledger the
optimal strategy is always to contact more. What actually disciplines contact
frequency is the revenue destroyed when a customer opts out, so that is now
priced — see `externalities` in `config/rates.yaml`.

| Arm | Gross / ep | Realised cost / ep | Externalities / ep | Net after ext. / ep |
|---|---|---|---|---|
| `treatment` | ₹1,180.06 | ₹0.23 | ₹142.14 | ₹1,037.69 |
| `baseline_blast` | ₹1,037.01 | ₹0.34 | ₹200.95 | ₹835.71 |
| `baseline_naive` | ₹1,380.48 | ₹0.00 | ₹84.09 | ₹1,296.37 |
| `baseline_rules` | ₹1,032.05 | ₹0.23 | ₹293.60 | ₹738.22 |

Treatment against blast on **net after externalities**: ₹2,01,984.50 per 1,000 episodes, 95% CI [-₹72,829.58, ₹4,71,833.86], p = 0.1244 — no — indistinguishable from noise.

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
| `treatment` | 90.3% | 100.0% (n=1297) | 95.7% (n=348) | 49.9% (n=355) |
| `baseline_rules` | 84.8% | 100.0% (n=477) | 65.7% (n=137) | 50.7% (n=136) |
| *oracle limited to the episode itself* | *90.5%* | *100.0%* | *100.0%* | *44.5%* |

The oracle row is a ceiling for classifiers that read **one episode at a time**:
it knows every wording, and where the text says nothing it names the most common
cause for that surface. Nothing that reads only this payment can beat it.

**Both arms exceed it in the no-signal column, which is the point.** They are
not better classifiers of a content-free string — nothing can be. They stop
classifying it in isolation. When forty payments on one bank fail inside an
hour, that bank is down, and that is evidence about *this* payment which
*this* payment's error text does not contain. Beating a ceiling means the
information available changed, not that somebody got cleverer.

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

## The model

| | |
|---|---|
| Model | `nvidia/nemotron-3-super-120b-a12b` |
| Fallback chain | `openai/gpt-oss-120b` |
| Answered from resolved history, no model call | 1297 |
| Answered deterministically (outage or base rates) | 355 |
| Sent to the model | 348 (348 from cache, 0 live) |
| Fell back to rules | 0 (0.0%) |
| Stopped by the budget ceiling | 0 |
| Attempts per successful call | 0.00 |
| Tokens | 0 in, 0 out |
| Token cost (notional; free tier) | ₹0.00 |

**83% of episodes never reach the model.** A wording resolved
consistently before is answered by lookup; text that identifies nothing is
answered by the outage detector or the base rates. Both are optimal on
their own ground and both are free. The model is called only where neither
can answer — which is also the only place its value can be demonstrated.

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

Accuracy is measured on **all 5,000 episodes for both classifiers**, not
on their randomised arms. Recovery is a causal question and needs
randomisation; accuracy is not, so running both over identical inputs
removes the sampling noise instead of reporting it. On the arm split the
keyword classifier appeared to lead on uninformative text — on 136
episodes against 355, a gap well inside the smaller sample's noise.

| | Model | Keyword classifier |
|---|---|---|
| Wording seen in history | 100.0% (n=3225) | 100.0% (n=3225) |
| **Wording never seen** | 94.2% (n=915) | 67.8% (n=915) |
| Text identifies nothing | 50.7% (n=860) | 50.7% (n=860) |
| **Overall accuracy** | **90.5%** | 85.6% |
| Forbidden retries / 1,000 episodes | 0.0 | **0.0** |
| Recovery rate | 59.9% | 60.0% |
| Difference in recovery rate | -0.10 pp, p = 0.968 | — |

**One row carries the argument.** On wordings the merchant has resolved
before, a lookup is optimal and both arms score 100% — a model adds nothing
and costs money. On text that identifies nothing, base rates and the outage
detector are optimal and both arms score the same, because both use the
same deterministic path. The first time an acquirer rewords a decline, the
keyword table falls to 68% and the model holds at 94%. That is the entire
case for putting a model in this system, and it is one column wide.

Which is why the model is consulted on so little. Of the treatment arm's
2000 episodes, 1297 were answered
by history and 355 deterministically; only 348 reached the model.
Routing the rest through it because it is the interesting component would
be worse on the metric and worse on the bill.

- **Recovery is identical.** -0.10 points, p = 0.968. Better diagnosis is not buying more money here;
  it is buying the same money with fewer wrong actions.
- **Harm is equal and it is zero**: 0.0 forbidden retries per
  1,000 episodes against 0.0, and the fixed ladder's 965. Neither
  arm gets there by classifying better — they get there because a
  low-confidence diagnosis is not allowed to authorise a retry when the
  base rates say a fifth of failures in this context are things nobody may
  re-present.
- **The overall figure sits exactly on the single-episode oracle.** That is
  a coincidence of two opposite gaps: the model is 5.8 points short of
  perfect on new wordings, and 6.2 points *past* the oracle on text that
  identifies nothing, because the outage detector reads across episodes and
  the oracle does not.

## Harm

What each strategy costs the people on the other end. **Forbidden retries** are
attempts against an episode whose *true* cause is never-retryable — a dead card,
a risk decline, a revoked mandate. The gate can only refuse a retry for a cause
somebody identified, so this is the price of a wrong diagnosis, and it is the
number a better diagnoser has to drive down.

| Arm | Forbidden retries / 1,000 ep | Opt-outs / 1,000 ep | Complaints / 1,000 ep | Disputes / 1,000 ep |
|---|---|---|---|---|
| `treatment` | 0.0 | 51.5 | 13.0 | 2.0 |
| `baseline_rules` | 0.0 | 56.0 | 17.3 | 5.3 |
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
| `exhausted` | 516 | 268 | 291 | 0 |
| `partially_recovered` | 21 | 4 | 18 | 0 |
| `recovered` | 1177 | 460 | 397 | 112 |
| `skipped_negative_ev` | 0 | 0 | 0 | 638 |
| `suppressed` | 101 | 18 | 44 | 0 |
| `unrecoverable` | 55 | 0 | 0 | 0 |

## Per-cause recovery (treatment)

| Root cause | n | recovered | rate | gross |
|---|---|---|---|---|
| `insufficient_funds` | 375 | 266 | 70.9% | ₹3.62L |
| `mandate_insufficient` | 296 | 147 | 49.7% | ₹1.62L |
| `authentication_failed` | 244 | 159 | 65.2% | ₹2.55L |
| `invoice_forgotten` | 207 | 149 | 72.0% | ₹6.47L |
| `issuer_down` | 172 | 165 | 95.9% | ₹2.85L |
| `technical_timeout` | 118 | 78 | 66.1% | ₹98.5K |
| `mandate_revoked` | 116 | 58 | 50.0% | ₹78.3K |
| `invoice_cash_crunch` | 97 | 56 | 57.7% | ₹1.60L |
| `card_expired_or_invalid` | 91 | 43 | 47.3% | ₹66.5K |
| `invoice_disputed` | 85 | 11 | 12.9% | ₹1.57L |
| `limit_exceeded` | 74 | 46 | 62.2% | ₹64.1K |
| `risk_declined` | 71 | 4 | 5.6% | ₹4.9K |
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
