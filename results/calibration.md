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
| `stratified` | permutation, rupees | 21/300 | 7.0% | consistent with 5% (p = 0.078) |
| `stratified` | permutation, recovery rate | 18/300 | 6.0% | consistent with 5% (p = 0.247) |
| `stratified` | bootstrap CI excludes 0, rupees | 13/300 | 4.3% | consistent with 5% (p = 0.739) |
| `stratified` | bootstrap CI excludes 0, rate | 21/300 | 7.0% | consistent with 5% (p = 0.078) |
| `simple` | permutation, rupees | 15/300 | 5.0% | consistent with 5% (p = 0.537) |
| `simple` | permutation, recovery rate | 11/300 | 3.7% | consistent with 5% (p = 0.888) |
| `simple` | bootstrap CI excludes 0, rupees | 19/300 | 6.3% | consistent with 5% (p = 0.175) |
| `simple` | bootstrap CI excludes 0, rate | 11/300 | 3.7% | consistent with 5% (p = 0.888) |

## 2. Is the test itself exact? One world, many splits

400 independent random re-splits of a single treatment arm. Conditional
on fixed data a permutation test is exact regardless of how that data was
generated, so this isolates the implementation from the simulator and from
seed-to-seed luck.

| Test | Rejections | Rate | |
|---|---|---|---|
| permutation, rupees | 17/400 | 4.2% | consistent with 5% (p = 0.786) |
| permutation, recovery rate | 19/400 | 4.8% | consistent with 5% (p = 0.623) |

## 3. What stratification actually buys

Not calibration — both designs randomise, and section 1 shows both rejecting at
about the same rate. The claim to test is **precision**: a narrower reference
distribution is a lower bar for a real effect to clear.

Two widths, because they answer different questions. The **null band** is the
middle 95% of the permutation distribution — the noise the decision rule has to
see past. The **bootstrap CI** is the descriptive interval.

| Design | Null band, rupees / ep | Null band, recovery pp | CI width, rupees / ep | CI width, pp |
|---|---|---|---|---|
| `stratified` | 50,519 | 8.56 | 58,634 | 8.57 |
| `simple` | 57,730 | 8.59 | 58,635 | 8.59 |

Stratifying narrows the null band by **12.5%** on rupees.

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
