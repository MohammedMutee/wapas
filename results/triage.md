# Wapas — is it ever right to refuse to chase a payment?

Seed `20260901` · 5,000 episodes · treatment arm · scorer trained on 5,000 worked history episodes.

## The probability model

P(recover | cause, surface, amount band) as a smoothed conditional rate over
episodes the merchant already worked, with backoff when a cell is thin. Not a
gradient-boosted model, which the build plan called for: what is observable
before the first action is a handful of low-cardinality categoricals, and an
empirical rate on that data is **calibrated by construction** rather than
calibrated afterwards by more machinery.

That claim is checkable, so here it is checked on episodes the scorer never saw:

| Predicted | Observed | n |
|---|---|---|
| 7.9% | 11.1% | 380 |
| 26.9% | 34.2% | 38 |
| 44.8% | 40.6% | 165 |
| 58.7% | 57.4% | 2,438 |
| 69.4% | 66.3% | 1,521 |
| 98.4% | 97.8% | 458 |

Expected calibration error **0.021**. A
probability that is only a ranking is useless here — multiplying a
miscalibrated 0.8 by an amount produces a confident wrong number.

## Sweep 1 — raising the bar

| EV floor | Episodes skipped | Recovery | Net / episode | Contacts / ep | Opt-outs |
|---|---|---|---|---|---|
| **off** | 0 | 59.6% | ₹1,028.33 | 0.89 | 5.38% |
| ₹0.00 | 576 | 58.0% | ₹1,007.88 | 0.85 | 5.20% (-2.0% net) |
| ₹5.00 | 576 | 58.0% | ₹1,007.88 | 0.85 | 5.20% (-2.0% net) |
| ₹50.00 | 585 | 57.9% | ₹1,007.82 | 0.85 | 5.20% (-2.0% net) |
| ₹200.00 | 1,493 | 49.1% | ₹992.26 | 0.69 | 4.22% (-3.5% net) |
| ₹500.00 | 2,628 | 37.8% | ₹936.00 | 0.50 | 3.24% (-9.0% net) |
| ₹1,000.00 | 3,548 | 28.9% | ₹846.22 | 0.33 | 2.08% (-17.7% net) |
| ₹3,000.00 | 4,613 | 18.5% | ₹595.15 | 0.10 | 0.80% (-42.1% net) |

**Every setting is worse than off, monotonically.** There is no floor at which
this pays. In this world recovery probability is high and the opt-out hazard is
low, so expected recovery dominates expected harm almost everywhere, and each
episode refused is mostly revenue given up.

So the feature ships **disabled**. `make eval` does not use it, and the headline
numbers do not depend on it.

## Sweep 2 — when would it pay?

A negative result is more useful with the condition attached. The externality
model is the most contestable number in this project; this varies it and asks
when refusing to chase starts earning its place.

| Opt-out costs | Net / ep, triage off | Net / ep, triage on | Skipped | |
|---|---|---|---|---|
| 1x our estimate | ₹1,028.33 | ₹1,007.88 | 576 | -2.0% |
| 2x our estimate | ₹857.60 | ₹851.43 | 599 | -0.7% |
| 3x our estimate | ₹686.88 | ₹691.41 | 599 | **triage wins** |
| 4x our estimate | ₹516.15 | ₹691.55 | 1,391 | **triage wins** |
| 6x our estimate | ₹174.70 | ₹638.38 | 1,444 | **triage wins** |
| 8x our estimate | -₹166.74 | ₹586.14 | 1,444 | **triage wins** |
| 12x our estimate | -₹849.63 | ₹481.66 | 1,444 | **triage wins** |

**The crossover is around 3x.** If losing a contactable customer
costs a merchant more than about 3 times what this project assumes,
triage stops being a cost and starts being the difference between an
operation that makes money and one that does not.

Which is the answer to the obvious objection. A reader who thinks the
externality pricing is too *low* is not undermining the system — they are
describing the conditions under which one of its switches should be on, and
the switch exists, is calibrated, and has a measured cost.

Reproduce: `python -m eval.triage_study --seed 20260901`
