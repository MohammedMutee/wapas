# Wapas — sensitivity sweep

Every parameter group scaled by ±30%, seed `20260901`, rules-only planner, 26 perturbed runs.

The question is not whether the numbers move — they will. It is whether the
**conclusions** move.

| Parameter | x | Incremental / 1,000 ep | Beats control? | vs naive (pp) | Loses to naive? | Forbidden retries / 1,000 | Net after ext. / ep |
|---|---|---|---|---|---|---|---|
| **baseline** | — | ₹9.02L | yes | -3.3 | no | 0.0 vs 965 | ₹1,018.43 |
| self-recovery rate | 0.7 | ₹8.63L | yes | -2.8 | no | 0.0 vs 965 | ₹942.86 |
| self-recovery rate | 1.3 | ₹9.33L | yes | -2.9 | no | 0.0 vs 965 | ₹1,027.57 |
| how much interventions help | 0.7 | ₹8.41L | yes | -3.4 | no | 0.0 vs 973 | ₹957.11 |
| how much interventions help | 1.3 | ₹9.83L | yes | -2.7 | no | 0.0 vs 939 | ₹1,101.88 |
| cause-by-intervention fit | 0.7 | ₹8.47L | yes | -4.1 | no | 0.0 vs 965 | ₹962.48 |
| cause-by-intervention fit | 1.3 | ₹9.71L | yes | -0.7 | no | 0.0 vs 965 | ₹1,099.31 |
| opt-out and complaint hazard | 0.7 | ₹9.14L | yes | -3.0 | no | 0.0 vs 965 | ₹1,064.68 |
| opt-out and complaint hazard | 1.3 | ₹8.80L | yes | -3.9 | no | 0.0 vs 965 | ₹943.06 |
| contact fatigue | 0.7 | ₹9.06L | yes | -3.1 | no | 0.0 vs 965 | ₹1,025.20 |
| contact fatigue | 1.3 | ₹8.96L | yes | -3.4 | no | 0.0 vs 965 | ₹1,012.84 |
| amount spread | 0.7 | ₹6.39L | yes | -1.4 | no | 0.0 vs 991 | ₹743.73 |
| amount spread | 1.3 | ₹13.25L | yes | -2.5 | no | 0.0 vs 981 | ₹1,520.28 |
| issuer outage frequency | 0.7 | ₹9.02L | yes | -3.2 | no | 0.0 vs 965 | ₹1,018.81 |
| issuer outage frequency | 1.3 | ₹8.91L | yes | -3.5 | no | 0.0 vs 965 | ₹1,003.88 |
| share of issuers an outage hits | 0.7 | ₹9.04L | yes | -3.2 | no | 0.0 vs 965 | ₹1,020.92 |
| share of issuers an outage hits | 1.3 | ₹8.94L | yes | -3.4 | no | 0.0 vs 965 | ₹1,006.69 |
| share of failures with no signal | 0.7 | ₹9.02L | yes | -2.9 | no | 2.0 vs 965 | ₹1,015.48 |
| share of failures with no signal | 1.3 | ₹8.93L | yes | -3.7 | no | 3.0 vs 965 | ₹1,007.67 |
| timing effects | 0.7 | ₹9.00L | yes | -3.5 | no | 0.0 vs 965 | ₹1,016.84 |
| timing effects | 1.3 | ₹9.03L | yes | -3.3 | no | 0.0 vs 965 | ₹1,019.43 |
| opt-out cost | 0.7 | ₹9.02L | yes | -3.3 | no | 0.0 vs 965 | ₹1,061.96 |
| opt-out cost | 1.3 | ₹9.02L | yes | -3.3 | no | 0.0 vs 965 | ₹974.89 |
| forbidden-retry penalty | 0.7 | ₹9.02L | yes | -3.3 | no | 0.0 vs 965 | ₹1,018.43 |
| forbidden-retry penalty | 1.3 | ₹9.02L | yes | -3.3 | no | 0.0 vs 965 | ₹1,018.43 |
| channel spend | 0.7 | ₹9.02L | yes | -3.3 | no | 0.0 vs 965 | ₹1,018.50 |
| channel spend | 1.3 | ₹9.02L | yes | -3.3 | no | 0.0 vs 965 | ₹1,018.36 |

## What holds

**The agent beats the randomised control arm in all 27 runs.** The headline
claim does not depend on any single parameter being right. The size varies
from ₹6.39L to ₹13.25L per 1,000 episodes,
which is the honest range to quote rather than the single central figure.

**It trails the fixed ladder in 27 of 27 runs, and never
significantly — 0 of 27 reach the 5% level.** Both halves
of that matter, and they point in opposite directions.

No single run can reject the null, so on the usual rule there is nothing to
report. But the *sign* is negative under every perturbation tried, including
ones that should favour a cause-aware planner, and a consistent sign across
independent perturbations is worth more than any one p-value. The honest
reading is that the rules planner is probably slightly behind the fixed ladder
on rupees, and this sweep cannot pin down by how much.

**The harm gap holds everywhere.** Across every run in this table the diagnosing arm performs at most 3.0 forbidden retries per 1,000
episodes, against the fixed ladder's ~965. That
conclusion is not sensitive to the assumptions at all, because it follows from
the fixed ladder not looking at the cause rather than from any number in the
parameter files.

## Parameters that do nothing

A knob that changes nothing is not reassurance, it is a finding: either the
result genuinely does not depend on it, or the mechanism it describes is not
wired to anything.

- **forbidden-retry penalty** — net after externalities unchanged at ±30%.
  This one is inert for a good reason rather than a bad one: it prices
  an event that no longer happens. Both diagnosing arms run zero
  forbidden retries, so the penalty per retry multiplies by nothing.

Each knob is judged on the metric it can actually move: a price knob cannot
change gross recovery by construction, and testing it on gross would report
every price in the rate card as inert. The first version of this section did
exactly that.

### The outage parameters are live now

`bursts_per_90_days` and `affected_issuer_share` both moved nothing until
2026-08-24, and D29 said so: the simulator argued for correlated outages while
nothing in the system consumed the correlation. Every episode was planned in
isolation, so the clustering was real in the data and invisible to the agent.

Both move the results now, because `FleetView` reads across episodes: forty
failures on one bank inside an hour is an outage, and that is evidence about a
payment which the payment's own error text does not contain. A parameter that
does nothing is a claim the code has not implemented, and the only way to find
out is to sweep it.

## The most contestable parameter

Externality pricing is a model, not a measurement, and it is the number a
sceptical reader should push on first. At ±30% on the
recoverable share, net after externalities moves between ₹974.89 and ₹1,061.96 per episode — 9% of the base figure.
Which is why the report prints net both with and without it.

Reproduce: `python -m eval.sensitivity --factor 0.3`
