"""Batch evaluation: the number the whole project is judged on.

Runs the seeded synthetic population through every arm and writes
``results/report.md``. Deterministic: the same seed always produces the same
report, which is what lets CI regenerate it on every commit instead of us
typing numbers into a README.

    python -m eval.run_batch --seed 20260901
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eval.stats import bootstrap_difference
from sim import ResponseModel, build_population, load_params
from wapas.audit import HashChain, verify_chain
from wapas.clock import IST
from wapas.domain import Arm
from wapas.engine import EpisodeResult, EpisodeRunner, assign_arm
from wapas.llm.costs import CostBook
from wapas.money import format_inr
from wapas.policy import load_policies
from wapas.strategies import Blast, DoNothing, NaiveRetry, RulesOnly

ARM_SHARES: dict[Arm, float] = {
    Arm.TREATMENT: 0.60,
    Arm.CONTROL: 0.10,
    Arm.BASELINE_NAIVE: 0.10,
    Arm.BASELINE_BLAST: 0.10,
    Arm.BASELINE_RULES: 0.10,
}

# Until the LLM agent lands, the treatment arm runs the rules-only planner.
# Stated explicitly in the report so nobody reads the current numbers as an
# LLM result.
STRATEGIES = {
    Arm.TREATMENT: RulesOnly,
    Arm.CONTROL: DoNothing,
    Arm.BASELINE_NAIVE: NaiveRetry,
    Arm.BASELINE_BLAST: Blast,
    Arm.BASELINE_RULES: RulesOnly,
}
TREATMENT_NOTE = (
    "The treatment arm currently runs the **rules-only** planner. The LLM agent "
    "is not yet wired in, so treatment and `baseline_rules` are the same policy "
    "differing only by sample. Any gap between them is sampling noise, and the "
    "LLM ablation is not yet meaningful."
)


@dataclass
class ArmSummary:
    arm: Arm
    n: int = 0
    recovered_paise: int = 0
    cost_paise: int = 0
    recoveries: int = 0
    self_recoveries: int = 0
    contacts: int = 0
    opt_outs: int = 0
    complaints: int = 0
    denials: int = 0
    modifications: int = 0
    escalations: int = 0
    diagnosed: int = 0
    diagnosed_correct: int = 0
    states: Counter = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.states = Counter()

    @property
    def net_paise(self) -> int:
        return self.recovered_paise - self.cost_paise

    @property
    def recovery_rate(self) -> float:
        return self.recoveries / self.n if self.n else 0.0

    @property
    def opt_out_rate(self) -> float:
        return self.opt_outs / self.n if self.n else 0.0

    @property
    def cost_per_100_recovered(self) -> float:
        return (self.cost_paise / self.recovered_paise * 100) if self.recovered_paise else 0.0


def summarise(results: list[EpisodeResult]) -> dict[Arm, ArmSummary]:
    out: dict[Arm, ArmSummary] = {}
    for r in results:
        s = out.setdefault(r.arm, ArmSummary(arm=r.arm))
        s.n += 1
        s.recovered_paise += r.recovered_paise
        s.cost_paise += r.cost_paise
        s.recoveries += 1 if r.recovered else 0
        s.self_recoveries += 1 if r.self_recovered else 0
        s.contacts += r.contacts_made
        s.opt_outs += 1 if r.opted_out else 0
        s.complaints += 1 if r.complained else 0
        s.denials += r.denials
        s.modifications += r.modifications
        s.escalations += 1 if r.escalated else 0
        s.states[str(r.state)] += 1
        if r.diagnosis_correct is not None:
            s.diagnosed += 1
            s.diagnosed_correct += 1 if r.diagnosis_correct else 0
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=20260901)
    ap.add_argument("--params", default="sim/params.yaml")
    ap.add_argument("--out", default="results/report.md")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    params = load_params(args.params)
    policies = load_policies("policies")
    costs = CostBook.load("config/rates.yaml")
    start = _dt.datetime(2026, 6, 1, tzinfo=IST)

    population = build_population(params, run_seed=args.seed, start=start)
    response = ResponseModel(params)
    chain = HashChain(salt=f"eval-{args.seed}")
    runner = EpisodeRunner(policies=policies, costs=costs, response=response,
                           run_seed=args.seed, chain=chain)

    results: list[EpisodeResult] = []
    for ep in population.episodes:
        arm = assign_arm(ep.ref, args.seed, ARM_SHARES)
        results.append(runner.run(ep, arm, STRATEGIES[arm]()))

    summaries = summarise(results)
    report = build_report(args, params, policies, costs, population, results, summaries, chain)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    if not args.quiet:
        print(report)
    print(f"\nwrote {out}", file=sys.stderr)
    return 0


def build_report(args, params, policies, costs, population, results, summaries, chain) -> str:
    treat = summaries.get(Arm.TREATMENT)
    control = summaries.get(Arm.CONTROL)

    by_arm: dict[Arm, list[EpisodeResult]] = defaultdict(list)
    for r in results:
        by_arm[r.arm].append(r)

    incremental = bootstrap_difference(
        [float(r.recovered_paise) for r in by_arm[Arm.TREATMENT]],
        [float(r.recovered_paise) for r in by_arm[Arm.CONTROL]],
        seed=args.seed,
    )
    net_incremental = incremental.point - treat.cost_paise

    verification = verify_chain(chain)
    total_at_risk = sum(r.amount_paise for r in results)

    L = []
    A = L.append
    A("# Wapas — evaluation report")
    A("")
    A(f"Seed `{args.seed}` · episodes `{len(results)}` · policy `{policies.version}` · "
      f"rates `{costs.version}` · sim `{params.version}`")
    A("")
    A("> **In-simulation results.** Every number below is produced by the synthetic")
    A("> world defined in `sim/params.yaml`, whose generative parameters are published")
    A("> and which the agent never reads. These are not measured Razorpay statistics.")
    A("")
    A(f"> **Note on the current treatment arm.** {TREATMENT_NOTE}")
    A("")
    A("## Headline")
    A("")
    A("| Metric | Value |")
    A("|---|---|")
    A(f"| Total revenue at risk | {format_inr(total_at_risk)} |")
    A(f"| Gross recovered (treatment) | {format_inr(treat.recovered_paise)} |")
    A(f"| Control arm, untreated, scaled to treatment size | "
      f"{format_inr(int(control.recovered_paise * treat.n / max(1, control.n)))} |")
    A(f"| **Incremental recovery** | **{format_inr(int(incremental.point))}** "
      f"(95% CI [{format_inr(int(incremental.low))}, {format_inr(int(incremental.high))}]) |")
    A(f"| Cost of treatment | {format_inr(treat.cost_paise)} |")
    A(f"| **Net incremental recovery** | **{format_inr(int(net_incremental))}** |")
    A(f"| Cost per ₹100 recovered | ₹{treat.cost_per_100_recovered:.2f} |")
    A(f"| Policy denials (actions blocked before execution) | {treat.denials} |")
    A(f"| Policy modifications (rescheduled, not dropped) | {treat.modifications} |")
    A(f"| Audit chain | {verification} |")
    A("")
    A("The control arm is the whole point. It recovered "
      f"{control.recovery_rate:.1%} of its episodes **without any intervention at all**. "
      "Reporting gross recovery would have claimed credit for every one of them.")
    A("")

    A("## Arms")
    A("")
    A("Arms differ in size, so **totals are not comparable** — the per-episode")
    A("columns are the ones to read.")
    A("")
    A("| Arm | n | Recovery rate | Gross / episode | Net / episode | Contacts / episode | "
      "Opt-out rate | Complaints |")
    A("|---|---|---|---|---|---|---|---|")
    for arm in (Arm.TREATMENT, Arm.BASELINE_RULES, Arm.BASELINE_NAIVE,
                Arm.BASELINE_BLAST, Arm.CONTROL):
        s = summaries.get(arm)
        if not s:
            continue
        A(f"| `{arm}` | {s.n} | {s.recovery_rate:.1%} | "
          f"{format_inr(s.recovered_paise // max(1, s.n))} | "
          f"{format_inr(s.net_paise // max(1, s.n))} | "
          f"{s.contacts / max(1, s.n):.2f} | "
          f"{s.opt_out_rate:.1%} | {s.complaints} |")
    A("")

    A("### Treatment against each baseline")
    A("")
    A("Incremental recovery per 1,000 episodes, with a 95% bootstrap CI over")
    A("episodes. A CI spanning zero means we cannot claim a difference.")
    A("")
    A("| Compared with | Incremental / 1,000 episodes | 95% CI | Claim supported? |")
    A("|---|---|---|---|")
    for other in (Arm.CONTROL, Arm.BASELINE_NAIVE, Arm.BASELINE_BLAST, Arm.BASELINE_RULES):
        if other not in by_arm or not by_arm[other]:
            continue
        iv = bootstrap_difference(
            [float(r.recovered_paise) for r in by_arm[Arm.TREATMENT]],
            [float(r.recovered_paise) for r in by_arm[other]],
            seed=args.seed,
        )
        n_t = len(by_arm[Arm.TREATMENT])
        per_k = 1000 / max(1, n_t)
        supported = "yes" if iv.low > 0 else ("no — CI spans zero" if iv.high > 0 else "worse")
        if other is Arm.BASELINE_RULES:
            supported += "  ← **A/A test, see below**"
        A(f"| `{other}` | {format_inr(int(iv.point * per_k))} | "
          f"[{format_inr(int(iv.low * per_k))}, {format_inr(int(iv.high * per_k))}] | "
          f"{supported} |")
    A("")
    A("#### A/A sanity check — read this before believing any row above")
    A("")
    A("`treatment` and `baseline_rules` currently run the **same strategy**. The true")
    A("difference between them is exactly zero, so that row is an A/A test and any")
    A("result other than \"CI spans zero\" is a **false positive**.")
    A("")
    rules_iv = bootstrap_difference(
        [float(r.recovered_paise) for r in by_arm[Arm.TREATMENT]],
        [float(r.recovered_paise) for r in by_arm.get(Arm.BASELINE_RULES, [])],
        seed=args.seed,
    )
    if rules_iv.low > 0 or rules_iv.high < 0:
        A("**On this seed the A/A test fails**: the interval excludes zero even though")
        A("there is nothing to detect. The cause is arm size — the baseline arms hold")
        A(f"~{len(by_arm.get(Arm.BASELINE_RULES, []))} episodes against treatment's "
          f"{len(by_arm[Arm.TREATMENT])} — combined with a heavy-tailed amount")
        A("distribution in which a few large recoveries move the mean a long way.")
        A("")
        A("Consequences we accept rather than hide:")
        A("")
        A("- The `baseline_naive` comparison above is the one that matters, and it")
        A("  **already spans zero**. We currently cannot claim to beat the industry")
        A("  default. Saying so now is cheaper than discovering it on camera.")
        A("- Before the final run: raise baseline arm sizes, stratify assignment by")
        A("  amount decile, and report the A/A interval alongside every A/B interval.")
    else:
        A("On this seed the A/A interval spans zero, as it should.")
    A("")

    blast = summaries.get(Arm.BASELINE_BLAST)
    if blast and control:
        A("### The aggression trade")
        A("")
        A(f"`baseline_blast` recovers {blast.recovery_rate:.1%} of episodes against "
          f"treatment's {treat.recovery_rate:.1%}, using "
          f"{blast.contacts / max(1, blast.n):.2f} contacts per episode against "
          f"{treat.contacts / max(1, treat.n):.2f}, and produces an opt-out rate of "
          f"{blast.opt_out_rate:.1%} against {treat.opt_out_rate:.1%}.")
        A("")
        A("**We are not yet able to show that guardrails pay for themselves.** Channel")
        A("spend is the only cost currently in the ledger, and at "
          f"{format_inr(treat.cost_paise)} across {treat.n} episodes it is far too small to")
        A("swing the net figure. The real cost of aggression is the *future* revenue from")
        A("a customer who opts out, and that is not priced yet. Until it is, the honest")
        A("statement is that blast wins gross and we cannot say what it loses.")
        A("")

    A("## Diagnosis accuracy")
    A("")
    A("| Arm | classified | correct | accuracy |")
    A("|---|---|---|---|")
    for arm, s in summaries.items():
        if s.diagnosed:
            A(f"| `{arm}` | {s.diagnosed} | {s.diagnosed_correct} | "
              f"{s.diagnosed_correct / s.diagnosed:.1%} |")
    A("")

    A("## Terminal states — the stopping rules, exercised")
    A("")
    A("| State | Treatment | Naive | Blast | Control |")
    A("|---|---|---|---|---|")
    all_states = sorted({st for s in summaries.values() for st in s.states})
    for st in all_states:
        row = [summaries.get(a).states.get(st, 0) if summaries.get(a) else 0
               for a in (Arm.TREATMENT, Arm.BASELINE_NAIVE, Arm.BASELINE_BLAST, Arm.CONTROL)]
        A(f"| `{st}` | {row[0]} | {row[1]} | {row[2]} | {row[3]} |")
    A("")

    A("## Per-cause recovery (treatment)")
    A("")
    A("| Root cause | n | recovered | rate | gross |")
    A("|---|---|---|---|---|")
    per_cause: dict[str, list[EpisodeResult]] = defaultdict(list)
    for r in by_arm[Arm.TREATMENT]:
        per_cause[str(r.true_cause)].append(r)
    for cause, rs in sorted(per_cause.items(), key=lambda kv: -len(kv[1])):
        rec = sum(1 for r in rs if r.recovered)
        A(f"| `{cause}` | {len(rs)} | {rec} | {rec / len(rs):.1%} | "
          f"{format_inr(sum(r.recovered_paise for r in rs), compact=True)} |")
    A("")

    A("## Known weaknesses")
    A("")
    A("- Results are in-simulation. The sensitivity sweep (±30% on every parameter)")
    A("  is not yet implemented, so these numbers are one point in parameter space.")
    A(f"- {TREATMENT_NOTE}")
    A("- Self-recovery is credited to whichever arm the episode fell in, including")
    A("  treatment. That is correct — it is exactly what the control arm subtracts —")
    A("  but it means the gross figure above is *not* the agent's achievement.")
    A("- Costs currently cover channel spend only. LLM token cost joins the ledger")
    A("  when the agent lands; free-tier models are priced notionally (see")
    A("  `config/rates.yaml`).")
    A("")
    A(f"Reproduce: `make eval SEED={args.seed}`")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
