# Wapas — A/A calibration

300 independent worlds, first seed `770001`, 2000 resamples per test.

Each row is a **null**: one arm split into two halves that ran the same
strategy. Any rejection is a false positive by construction. The nominal rate
is 5%. The verdict column is an exact binomial test of the observed rejection
count against 5%, one-sided — a rate that merely *looks* high is not evidence
that it is high.

## 1. False positives across worlds — the procedure as the report runs it

| Design | Test | Rejections | Rate | |
|---|---|---|---|---|
| `stratified` | permutation, rupees | 20/300 | 6.7% | consistent with 5% (p = 0.119) |
| `stratified` | permutation, recovery rate | 20/300 | 6.7% | consistent with 5% (p = 0.119) |
| `stratified` | bootstrap CI excludes 0, rupees | 12/300 | 4.0% | consistent with 5% (p = 0.822) |
| `stratified` | bootstrap CI excludes 0, rate | 21/300 | 7.0% | consistent with 5% (p = 0.078) |
| `simple` | permutation, rupees | 20/300 | 6.7% | consistent with 5% (p = 0.119) |
| `simple` | permutation, recovery rate | 13/300 | 4.3% | consistent with 5% (p = 0.739) |
| `simple` | bootstrap CI excludes 0, rupees | 21/300 | 7.0% | consistent with 5% (p = 0.078) |
| `simple` | bootstrap CI excludes 0, rate | 12/300 | 4.0% | consistent with 5% (p = 0.822) |

## 2. Is the test itself exact? One world, many splits

400 independent random re-splits of a single treatment arm. Conditional
on fixed data a permutation test is exact regardless of how that data was
generated, so this isolates the implementation from the simulator and from
seed-to-seed luck.

| Test | Rejections | Rate | |
|---|---|---|---|
| permutation, rupees | 17/400 | 4.2% | consistent with 5% (p = 0.786) |
| permutation, recovery rate | 13/400 | 3.2% | consistent with 5% (p = 0.964) |

## 3. What stratification actually buys

Not calibration — precision. Both designs randomise, so both should reject at
5% under a null. What differs is how *wide* the null difference is, and a
narrower null is a lower bar for a real effect to clear.

| Design | Mean 95% CI width, rupees / episode | Mean 95% CI width, recovery pp |
|---|---|---|
| `stratified` | 59,021 | 8.39 |
| `simple` | 59,027 | 8.39 |

## Reading this

- The **permutation** rows are the decision rule `results/report.md` uses.
  Section 2 is the authoritative check on whether that rule is sound; section 1
  has far fewer replications and its rows will bounce around.
- The **bootstrap CI** rows are the known weak point. A percentile bootstrap on
  a heavy-tailed difference of means is approximate. That is exactly why the
  report lets the p-value decide and treats the interval as descriptive.
- Eight rows are printed and each is tested at 5%, so on a correctly calibrated
  procedure one row crossing the line is the expected outcome, not a finding.
  Read section 1 as a smoke alarm, not as a verdict.

Reproduce: `make calibrate SEEDS=300`
