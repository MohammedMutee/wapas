# Wapas — sensitivity sweep

Every parameter group scaled by ±30%, seed `20260901`, rules-only planner, 24 perturbed runs.

The question is not whether the numbers move — they will. It is whether the
**conclusions** move.

| Parameter | x | Incremental / 1,000 ep | Beats control? | vs naive (pp) | Loses to naive? | Harm ratio | Net after ext. / ep |
|---|---|---|---|---|---|---|---|
| **baseline** | — | ₹9.08L | yes | -3.0 | no | 21x | ₹1,030.70 |
| self-recovery rate | 0.7 | ₹8.72L | yes | -2.6 | no | 21x | ₹960.00 |
| self-recovery rate | 1.3 | ₹9.40L | yes | -2.5 | no | 21x | ₹1,041.31 |
| how much interventions help | 0.7 | ₹8.57L | yes | -2.8 | no | 22x | ₹979.22 |
| how much interventions help | 1.3 | ₹9.94L | yes | -2.4 | no | 22x | ₹1,117.22 |
| cause-by-intervention fit | 0.7 | ₹8.57L | yes | -3.6 | no | 21x | ₹978.27 |
| cause-by-intervention fit | 1.3 | ₹9.77L | yes | -0.3 | no | 21x | ₹1,111.37 |
| opt-out and complaint hazard | 0.7 | ₹9.21L | yes | -2.6 | no | 21x | ₹1,076.19 |
| opt-out and complaint hazard | 1.3 | ₹8.86L | yes | -3.6 | no | 21x | ₹959.69 |
| contact fatigue | 0.7 | ₹9.11L | yes | -3.0 | no | 21x | ₹1,034.55 |
| contact fatigue | 1.3 | ₹9.02L | yes | -3.2 | no | 21x | ₹1,025.11 |
| amount spread | 0.7 | ₹6.43L | yes | -1.3 | no | 23x | ₹755.29 |
| amount spread | 1.3 | ₹13.36L | yes | -2.3 | no | 22x | ₹1,537.33 |
| issuer outage frequency | 0.7 | ₹9.08L | yes | -3.0 | no | 21x | ₹1,030.70 |
| issuer outage frequency | 1.3 | ₹9.08L | yes | -3.0 | no | 21x | ₹1,030.70 |
| share of failures with no signal | 0.7 | ₹9.06L | yes | -2.8 | no | 28x | ₹1,029.31 |
| share of failures with no signal | 1.3 | ₹9.01L | yes | -3.3 | no | 17x | ₹1,020.25 |
| timing effects | 0.7 | ₹9.06L | yes | -3.2 | no | 21x | ₹1,029.32 |
| timing effects | 1.3 | ₹9.09L | yes | -3.0 | no | 21x | ₹1,032.07 |
| opt-out cost | 0.7 | ₹9.08L | yes | -3.0 | no | 21x | ₹1,072.59 |
| opt-out cost | 1.3 | ₹9.08L | yes | -3.0 | no | 21x | ₹988.81 |
| forbidden-retry penalty | 0.7 | ₹9.08L | yes | -3.0 | no | 21x | ₹1,030.97 |
| forbidden-retry penalty | 1.3 | ₹9.08L | yes | -3.0 | no | 21x | ₹1,030.43 |
| channel spend | 0.7 | ₹9.08L | yes | -3.0 | no | 21x | ₹1,030.76 |
| channel spend | 1.3 | ₹9.08L | yes | -3.0 | no | 21x | ₹1,030.64 |

## What holds

**The agent beats the randomised control arm in all 25 runs.** The headline
claim does not depend on any single parameter being right. The size varies
from ₹6.43L to ₹13.36L per 1,000 episodes,
which is the honest range to quote rather than the single central figure.

**It trails the fixed ladder in 25 of 25 runs, and never
significantly — 0 of 25 reach the 5% level.** Both halves
of that matter, and they point in opposite directions.

No single run can reject the null, so on the usual rule there is nothing to
report. But the *sign* is negative under every perturbation tried, including
ones that should favour a cause-aware planner, and a consistent sign across
independent perturbations is worth more than any one p-value. The honest
reading is that the rules planner is probably slightly behind the fixed ladder
on rupees, and this sweep cannot pin down by how much.

**The harm gap holds everywhere.** The fixed ladder performs between 17x and 28x as many forbidden retries as the
diagnosing arm across every run in this table. That conclusion is not sensitive
to the assumptions at all, because it follows from the fixed ladder not looking
at the cause rather than from any number in the parameter files.

## Parameters that do nothing

A knob that changes nothing is not reassurance, it is a finding: either the
result genuinely does not depend on it, or the mechanism it describes is not
wired to anything.

- **issuer outage frequency** — incremental recovery unchanged at ±30%.

Each knob is judged on the metric it can actually move: a price knob cannot
change gross recovery by construction, and testing it on gross would report
every price in the rate card as inert. The first version of this section did
exactly that.

`issuer outage frequency` is the interesting one. `sim/signals.py` and the
parameter file both argue at length that outages must be modelled as
**correlated bursts** rather than independent draws, because i.i.d. downtime
would let a fixed retry ladder do nearly as well as a cause-aware one and
make timing intelligence look worthless. That argument is correct. It is also,
on this evidence, currently doing no work: moving the burst count from 14 to
10 or 18 leaves every headline figure identical.

The reason is that nothing in the system consumes the correlation. Every
episode is diagnosed and planned in isolation, so what reaches the response
model is only *this* payment's distance from *its* outage ending — a marginal
quantity that the burst count does not change. The clustering is real in the
data and invisible to the agent.

What would make it matter is an agent that reads across episodes: a spike of
`issuer_down` failures on one bank is observable, and the right response is to
hold every retry against that issuer until it recovers, not to rediscover the
outage one episode at a time. That is a real capability and a real product
feature, and it is not built. Until it is, the bursty outage model should be
described as a property of the simulator rather than as something the results
depend on.

## The most contestable parameter

Externality pricing is a model, not a measurement, and it is the number a
sceptical reader should push on first. At ±30% on the
recoverable share, net after externalities moves between ₹988.81 and ₹1,072.59 per episode — 8% of the base figure.
Which is why the report prints net both with and without it.

Reproduce: `python -m eval.sensitivity --factor 0.3`
