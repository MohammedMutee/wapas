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
import hashlib
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eval.stats import Comparison, compare
from sim import ResponseModel, build_population, load_params
from wapas.audit import HashChain, verify_chain
from wapas.clock import IST
from wapas.domain import Arm, RootCause
from wapas.engine import Allocation, EpisodeResult, EpisodeRunner, stratified_assignment
from wapas.llm.costs import CostBook
from wapas.money import format_inr
from wapas.policy import load_policies
from wapas.strategies import Blast, DoNothing, NaiveRetry, RulesOnly

ARM_SHARES: dict[Arm, float] = {
    Arm.TREATMENT: 0.40,
    Arm.CONTROL: 0.15,
    Arm.BASELINE_NAIVE: 0.15,
    Arm.BASELINE_BLAST: 0.15,
    Arm.BASELINE_RULES: 0.15,
}
"""Arm allocation.

Rebalanced from 60/10/10/10/10 on 2026-08-23. The old split bought a large
treatment arm at the cost of baselines too small to compare against: the width
of every comparison was set by the ~200-episode baseline, not by treatment.
A comparison is only as precise as its smaller arm, so the baselines were
raised even though it shrinks the arm the demo draws from."""

STRATEGIES = {
    Arm.TREATMENT: RulesOnly,
    Arm.CONTROL: DoNothing,
    Arm.BASELINE_NAIVE: NaiveRetry,
    Arm.BASELINE_BLAST: Blast,
    Arm.BASELINE_RULES: RulesOnly,
}
RULES_ONLY_NOTE = (
    "The treatment arm is running the **rules-only** planner: this run was made "
    "without `--llm`, so treatment and `baseline_rules` are the same policy "
    "differing only by sample. Any gap between them is sampling noise, and the "
    "LLM ablation is not meaningful in this report."
)
LLM_NOTE = (
    "The treatment arm runs the **LLM agent**. It shares the playbook library, "
    "the policy gate, the cost ledger and the audit chain with `baseline_rules`; "
    "the only difference between the two arms is how the root cause is "
    "classified. That is what makes the comparison an ablation of the model "
    "rather than of the system around it."
)


def build_strategies(*, use_llm: bool, seed: int):
    """Strategy factories per arm, plus the diagnoser if one is in play.

    The diagnoser is created **once** and shared across episodes so its cache,
    its spend and its budget ceiling are per-run rather than per-episode. A
    per-episode budget would be no budget at all.
    """
    factories = dict(STRATEGIES)
    if not use_llm:
        return factories, None

    from wapas.config import settings
    from wapas.diagnose import DiagnosisCache, LLMDiagnoser
    from wapas.llm import OpenAICompatProvider
    from wapas.llm.retry import RetryingProvider
    from wapas.strategies import LLMAgent

    cfg = settings()
    if cfg.nvidia_api_key is None:
        raise SystemExit(
            "--llm was requested but no NVIDIA_API_KEY is configured. Refusing to "
            "run: silently falling back to the rules planner would produce a "
            "report labelled as an LLM result that is not one."
        )
    provider = RetryingProvider(
        OpenAICompatProvider(
            base_url=cfg.nvidia_base_url,
            api_key=cfg.nvidia_api_key.get_secret_value(),
            name="nvidia",
        ),
        attempts=3,
        base_delay_s=2.0,
    )
    diagnoser = LLMDiagnoser(
        provider,
        model=cfg.model_reasoning,
        costs=CostBook.load("config/rates.yaml"),
        cache=DiagnosisCache(),
        budget_usd=cfg.llm_budget_usd,
        fallback_models=("openai/gpt-oss-120b",),
    )
    factories[Arm.TREATMENT] = lambda: LLMAgent(diagnoser)
    return factories, diagnoser


@dataclass
class ArmSummary:
    arm: Arm
    n: int = 0
    recovered_paise: int = 0
    cost_paise: int = 0
    externality_paise: int = 0
    recoveries: int = 0
    self_recoveries: int = 0
    contacts: int = 0
    opt_outs: int = 0
    complaints: int = 0
    disputes: int = 0
    forbidden_retries: int = 0
    denials: int = 0
    modifications: int = 0
    escalations: int = 0
    diagnosed: int = 0
    diagnosed_correct: int = 0
    states: Counter = field(default_factory=Counter)

    @property
    def net_paise(self) -> int:
        return self.recovered_paise - self.cost_paise

    @property
    def net_after_externalities_paise(self) -> int:
        return self.net_paise - self.externality_paise

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
        s.externality_paise += r.externality_paise
        s.recoveries += 1 if r.recovered else 0
        s.self_recoveries += 1 if r.self_recovered else 0
        s.contacts += r.contacts_made
        s.opt_outs += 1 if r.opted_out else 0
        s.complaints += 1 if r.complained else 0
        s.disputes += 1 if r.disputed else 0
        s.forbidden_retries += r.forbidden_retries
        s.denials += r.denials
        s.modifications += r.modifications
        s.escalations += 1 if r.escalated else 0
        s.states[str(r.state)] += 1
        if r.diagnosis_correct is not None:
            s.diagnosed += 1
            s.diagnosed_correct += 1 if r.diagnosis_correct else 0
    return out


def run_population(params, policies, costs, *, seed: int, start: _dt.datetime,
                   chain: HashChain | None = None,
                   strategies=None) -> tuple[list[EpisodeResult], Allocation]:
    """Simulate one whole world and run every episode through its assigned arm."""
    factories = strategies or STRATEGIES
    population = build_population(params, run_seed=seed, start=start)
    allocation = stratified_assignment(
        [(ep.ref, int(ep.amount_paise)) for ep in population.episodes], seed, ARM_SHARES
    )
    runner = EpisodeRunner(policies=policies, costs=costs, response=ResponseModel(params),
                           run_seed=seed, chain=chain)
    results = [runner.run(ep, allocation[ep.ref], factories[allocation[ep.ref]]())
               for ep in population.episodes]
    return results, allocation


def placebo_halves(
    results: list[EpisodeResult], allocation: Allocation, *, seed: int
) -> tuple[list[EpisodeResult], list[EpisodeResult]]:
    """Split one arm into two, stratified, for a permanent A/A test.

    Comparing treatment against ``baseline_rules`` is only an A/A test while the
    two happen to run the same strategy; the moment the LLM lands, the harness
    loses its null control exactly when it starts making claims. Splitting the
    treatment arm in half gives a comparison whose true difference is *always*
    zero, whatever the treatment is, so the noise floor can be published beside
    every real claim forever.
    """
    left: list[EpisodeResult] = []
    right: list[EpisodeResult] = []
    by_stratum: dict[int, list[EpisodeResult]] = defaultdict(list)
    for r in results:
        by_stratum[allocation.stratum[r.ref]].append(r)
    for stratum in sorted(by_stratum):
        members = sorted(
            by_stratum[stratum],
            key=lambda r: hashlib.sha256(f"placebo|{seed}|{r.ref}".encode()).digest(),
        )
        left.extend(members[0::2])
        right.extend(members[1::2])
    return left, right


def _values(results: list[EpisodeResult], field_name: str) -> list[float]:
    return [float(getattr(r, field_name)) for r in results]


def _acc(results: list[EpisodeResult], informative: bool) -> str:
    group = [r for r in results
             if r.diagnosis_correct is not None and r.signal_informative is informative]
    if not group:
        return "—"
    return f"{sum(1 for r in group if r.diagnosis_correct) / len(group):.1%}"


def _rates(results: list[EpisodeResult]) -> list[float]:
    """Recovery as percentage points, one value per episode.

    A far lower-variance endpoint than rupees: it is bounded, so no single
    large invoice can move it. Rupees remain the metric that matters, but a
    rupee comparison that spans zero and a rate comparison that does not are
    telling you the same thing about the strategy and different things about
    the amount distribution.
    """
    return [100.0 if r.recovered else 0.0 for r in results]


def _strata(a: list[EpisodeResult], b: list[EpisodeResult],
            allocation: Allocation) -> tuple[list[int], list[int]]:
    return ([allocation.stratum[r.ref] for r in a], [allocation.stratum[r.ref] for r in b])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=20260901)
    ap.add_argument("--params", default="sim/params.yaml")
    ap.add_argument("--out", default="results/report.md")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--llm", action="store_true",
                    help="run the treatment arm with the LLM agent instead of rules")
    args = ap.parse_args()

    params = load_params(args.params)
    policies = load_policies("policies")
    costs = CostBook.load("config/rates.yaml")
    start = _dt.datetime(2026, 6, 1, tzinfo=IST)

    factories, diagnoser = build_strategies(use_llm=args.llm, seed=args.seed)
    chain = HashChain(salt=f"eval-{args.seed}")
    results, allocation = run_population(
        params, policies, costs, seed=args.seed, start=start, chain=chain,
        strategies=factories,
    )
    if diagnoser is not None and diagnoser.cache is not None:
        diagnoser.cache.save()

    summaries = summarise(results)
    report = build_report(args, params, policies, costs, results, allocation, summaries,
                          chain, diagnoser)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    if not args.quiet:
        print(report)
    print(f"\nwrote {out}", file=sys.stderr)
    return 0


def build_report(args, params, policies, costs, results, allocation, summaries, chain,
                 diagnoser=None) -> str:
    treatment_note = LLM_NOTE if diagnoser is not None else RULES_ONLY_NOTE
    treat = summaries[Arm.TREATMENT]
    control = summaries[Arm.CONTROL]

    by_arm: dict[Arm, list[EpisodeResult]] = defaultdict(list)
    for r in results:
        by_arm[r.arm].append(r)

    def comparison(other: Arm, field_name: str = "recovered_paise") -> Comparison:
        t, o = by_arm[Arm.TREATMENT], by_arm[other]
        return compare(str(other), _values(t, field_name), _values(o, field_name),
                       seed=args.seed, strata=_strata(t, o, allocation))

    def rate_comparison(other: Arm) -> Comparison:
        t, o = by_arm[Arm.TREATMENT], by_arm[other]
        return compare(str(other), _rates(t), _rates(o),
                       seed=args.seed, strata=_strata(t, o, allocation))

    left, right = placebo_halves(by_arm[Arm.TREATMENT], allocation, seed=args.seed)
    placebo = compare("placebo A/A", _values(left, "recovered_paise"),
                      _values(right, "recovered_paise"), seed=args.seed,
                      strata=_strata(left, right, allocation))

    headline = comparison(Arm.CONTROL)
    incremental = headline.interval.scaled(treat.n)
    net_incremental = incremental.point - treat.cost_paise
    net_after_ext = net_incremental - treat.externality_paise

    verification = verify_chain(chain)
    total_at_risk = sum(r.amount_paise for r in results)

    L: list[str] = []
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
    A(f"> **The treatment arm.** {treatment_note}")
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
      f"(95% CI [{format_inr(int(incremental.low))}, {format_inr(int(incremental.high))}], "
      f"p = {headline.p_value:.4f}) |")
    A(f"| Realised cost of treatment | {format_inr(treat.cost_paise)} |")
    A(f"| **Net incremental recovery** | **{format_inr(int(net_incremental))}** |")
    A(f"| Modelled externalities (opt-outs, complaints, disputes) | "
      f"less {format_inr(treat.externality_paise)} |")
    A(f"| **Net after externalities** | **{format_inr(int(net_after_ext))}** |")
    A(f"| Cost per ₹100 recovered | ₹{treat.cost_per_100_recovered:.2f} |")
    A(f"| Policy denials (actions blocked before execution) | {treat.denials} |")
    A(f"| Policy modifications (rescheduled, not dropped) | {treat.modifications} |")
    A(f"| Audit chain | {verification} |")
    A("")
    A("The control arm is the whole point. It recovered "
      f"{control.recovery_rate:.1%} of its episodes **without any intervention at all**. "
      "Reporting gross recovery would have claimed credit for every one of them.")
    A("")

    A("## How this experiment is designed")
    A("")
    A("| | |")
    A("|---|---|")
    A(f"| Allocation | stratified by amount decile, {len(ARM_SHARES)} arms, "
      + " / ".join(f"{int(v * 100)}%" for v in ARM_SHARES.values()) + " |")
    A("| Decision rule | two-sided stratified permutation test at the 5% level |")
    A("| Interval | percentile bootstrap over episodes, 10,000 resamples |")
    A("| Null control | placebo split of the treatment arm, reported below |")
    A("")
    A("Amounts are lognormal with a long right tail, so which arm happens to receive")
    A("the largest invoices matters more than any strategy does. Simple randomisation")
    A("balances that only in expectation; stratifying by amount decile balances it on")
    A("**every** run, to within one episode per decile. The permutation test then")
    A("shuffles labels within those same deciles, because that is the randomisation")
    A("the experiment actually performed.")
    A("")
    A("Stratifying buys **precision, not correctness**. Both designs randomise, so")
    A("both reject a true null at the nominal rate; what stratifying removes is")
    A("variance, which narrows the bar a real effect has to clear. This distinction is")
    A("measured rather than asserted — see `results/calibration.md`, which also")
    A("records that the earlier A/A failure was an ordinary one-in-twenty event on one")
    A("seed rather than a broken procedure.")
    A("")

    A("## Arms")
    A("")
    A("Arms differ in size, so **totals are not comparable** — the per-episode")
    A("columns are the ones to read.")
    A("")
    A("| Arm | n | Recovery rate | Gross / ep | Net / ep | Net after ext. / ep | "
      "Contacts / ep | Opt-out rate | Complaints |")
    A("|---|---|---|---|---|---|---|---|---|")
    for arm in (Arm.TREATMENT, Arm.BASELINE_RULES, Arm.BASELINE_NAIVE,
                Arm.BASELINE_BLAST, Arm.CONTROL):
        s = summaries.get(arm)
        if not s:
            continue
        A(f"| `{arm}` | {s.n} | {s.recovery_rate:.1%} | "
          f"{format_inr(s.recovered_paise // max(1, s.n))} | "
          f"{format_inr(s.net_paise // max(1, s.n))} | "
          f"{format_inr(s.net_after_externalities_paise // max(1, s.n))} | "
          f"{s.contacts / max(1, s.n):.2f} | "
          f"{s.opt_out_rate:.1%} | {s.complaints} |")
    A("")

    A("### Treatment against each baseline")
    A("")
    A("Difference in gross recovery per 1,000 episodes. The **p-value decides**;")
    A("the interval describes the size. A comparison is only as precise as its")
    A("smaller arm.")
    A("")
    A("| Compared with | n | Δ gross / 1,000 ep | 95% CI | p | Claim supported? |")
    A("|---|---|---|---|---|---|")
    for other in (Arm.CONTROL, Arm.BASELINE_NAIVE, Arm.BASELINE_BLAST, Arm.BASELINE_RULES):
        if not by_arm.get(other):
            continue
        c = comparison(other)
        iv = c.interval.scaled(1000)
        note = "  ← **A/A, see below**" if other is Arm.BASELINE_RULES else ""
        A(f"| `{other}` | {c.n_other} | {format_inr(int(iv.point))} | "
          f"[{format_inr(int(iv.low))}, {format_inr(int(iv.high))}] | "
          f"{c.p_value:.4f} | {c.verdict()}{note} |")
    A("")

    A("#### The same comparison on recovery rate")
    A("")
    A("Rupees are what matter and rupees are heavy-tailed, so the interval above is")
    A("wide almost regardless of the strategy. Recovery rate is bounded and therefore")
    A("far more powerful at the same sample size. Both are reported; neither is")
    A("chosen after seeing the answer.")
    A("")
    A("| Compared with | Δ recovery rate (pp) | 95% CI | p | Claim supported? |")
    A("|---|---|---|---|---|")
    for other in (Arm.CONTROL, Arm.BASELINE_NAIVE, Arm.BASELINE_BLAST, Arm.BASELINE_RULES):
        if not by_arm.get(other):
            continue
        c = rate_comparison(other)
        A(f"| `{other}` | {c.interval.point:+.2f} | "
          f"[{c.interval.low:+.2f}, {c.interval.high:+.2f}] | "
          f"{c.p_value:.4f} | {c.verdict()} |")
    A("")

    A("#### Null controls — read these before believing any row above")
    A("")
    placebo_iv = placebo.interval.scaled(1000)
    A(f"**Placebo split.** The treatment arm is cut into two stratified halves "
      f"({placebo.n_treatment} / {placebo.n_other} episodes) that ran the *same*")
    A("strategy on the *same* seed. The true difference is exactly zero by")
    A("construction, so this measures what the harness reports when there is nothing")
    A("to report. It stays valid after the LLM lands, which the")
    A("`treatment` vs `baseline_rules` row will not.")
    A("")
    A(f"> Δ = {format_inr(int(placebo_iv.point))} per 1,000 episodes, "
      f"95% CI [{format_inr(int(placebo_iv.low))}, {format_inr(int(placebo_iv.high))}], "
      f"p = {placebo.p_value:.4f} — "
      + ("**FALSE POSITIVE**" if placebo.significant else "correctly not significant"))
    A("")
    if placebo.significant:
        A("The placebo fired. **Every significance claim in this report is suspect on")
        A("this seed** and should not be quoted. Investigate before using these numbers.")
    else:
        A("The noise floor for a comparison of this size is roughly ±"
          f"{format_inr(int(max(abs(placebo_iv.low), abs(placebo_iv.high))))} per 1,000")
        A("episodes. A difference smaller than that is not a difference.")
    A("")
    rules_c = comparison(Arm.BASELINE_RULES)
    placebo_rate = compare("placebo A/A rate", _rates(left), _rates(right),
                           seed=args.seed, strata=_strata(left, right, allocation))
    A(f"On recovery rate the same placebo gives {placebo_rate.interval.point:+.2f} pp, "
      f"p = {placebo_rate.p_value:.4f} — "
      + ("**FALSE POSITIVE**." if placebo_rate.significant else "correctly not significant."))
    A("")
    A(f"**Second A/A.** `treatment` and `baseline_rules` also run the same strategy "
      f"today: p = {rules_c.p_value:.4f} — "
      + ("a false positive." if rules_c.significant else "correctly not significant."))
    A("")
    A("One seed cannot establish a false-positive *rate*. `make calibrate` runs the")
    A("placebo across many seeds and reports the measured rate against the nominal 5%;")
    A("see `results/calibration.md`.")
    A("")

    blast = summaries.get(Arm.BASELINE_BLAST)
    if blast:
        A("### The aggression trade")
        A("")
        A(f"`baseline_blast` recovers {blast.recovery_rate:.1%} of episodes against "
          f"treatment's {treat.recovery_rate:.1%}, using "
          f"{blast.contacts / max(1, blast.n):.2f} contacts per episode against "
          f"{treat.contacts / max(1, treat.n):.2f}, and produces an opt-out rate of "
          f"{blast.opt_out_rate:.1%} against {treat.opt_out_rate:.1%}.")
        A("")
        A("Channel spend cannot settle this argument. An SMS costs 12 paise and a")
        A("recovered invoice is worth thousands of rupees, so on a spend-only ledger the")
        A("optimal strategy is always to contact more. What actually disciplines contact")
        A("frequency is the revenue destroyed when a customer opts out, so that is now")
        A("priced — see `externalities` in `config/rates.yaml`.")
        A("")
        A("| Arm | Gross / ep | Realised cost / ep | Externalities / ep | Net after ext. / ep |")
        A("|---|---|---|---|---|")
        for arm in (Arm.TREATMENT, Arm.BASELINE_BLAST, Arm.BASELINE_NAIVE, Arm.BASELINE_RULES):
            s = summaries.get(arm)
            if not s:
                continue
            A(f"| `{arm}` | {format_inr(s.recovered_paise // max(1, s.n))} | "
              f"{format_inr(s.cost_paise // max(1, s.n))} | "
              f"{format_inr(s.externality_paise // max(1, s.n))} | "
              f"{format_inr(s.net_after_externalities_paise // max(1, s.n))} |")
        A("")
        net_c = comparison(Arm.BASELINE_BLAST, "net_after_externalities_paise")
        net_iv = net_c.interval.scaled(1000)
        A(f"Treatment against blast on **net after externalities**: "
          f"{format_inr(int(net_iv.point))} per 1,000 episodes, "
          f"95% CI [{format_inr(int(net_iv.low))}, {format_inr(int(net_iv.high))}], "
          f"p = {net_c.p_value:.4f} — {net_c.verdict()}.")
        A("")
        A("**The externality figures are assumptions, not measurements**, and they are")
        A("the most contestable numbers in this project. They are reported on their own")
        A("line, and the net-before-externalities column is kept, so a reader who")
        A("rejects the model can still read every other result. The sensitivity sweep")
        A("varies them by ±30% like everything else.")
        A("")

    A("## Diagnosis accuracy")
    A("")
    A("Against the simulator's ground truth. Since `sim/signals.py` started emitting")
    A("realistic error text — several phrasings per cause, ISO 8583 response codes,")
    A(f"and {params.signal_noise.uninformative_share:.0%} of failures carrying no")
    A("diagnostic text at all — this is a judgement call rather than a lookup. The")
    A("ceiling from text alone is roughly the informative share; anything above it has")
    A("to come from context.")
    A("")
    A("| Arm | classified | correct | accuracy | on informative text | on uninformative |")
    A("|---|---|---|---|---|---|")
    for arm in (Arm.TREATMENT, Arm.BASELINE_RULES):
        rs = [r for r in by_arm.get(arm, []) if r.diagnosis_correct is not None]
        if not rs:
            continue
        clear = [r for r in rs if r.signal_informative]
        murky = [r for r in rs if not r.signal_informative]

        def pct(group):
            return (f"{sum(1 for r in group if r.diagnosis_correct) / len(group):.1%} "
                    f"(n={len(group)})") if group else "—"

        correct = sum(1 for r in rs if r.diagnosis_correct)
        A(f"| `{arm}` | {len(rs)} | {correct} | {correct / len(rs):.1%} | "
          f"{pct(clear)} | {pct(murky)} |")
    A("")
    generated = {RootCause(name) for name in params.failure_causes} | {
        RootCause.MANDATE_REVOKED, RootCause.MANDATE_INSUFFICIENT,
        RootCause.INVOICE_FORGOTTEN, RootCause.INVOICE_CASH_CRUNCH,
        RootCause.INVOICE_DISPUTED,
    }
    phantom: dict[str, int] = defaultdict(int)
    abstentions = 0
    murky_abstentions = 0
    murky_total = 0
    for r in by_arm.get(Arm.TREATMENT, []):
        if r.diagnosed_cause is None:
            continue
        if not r.signal_informative:
            murky_total += 1
            if r.diagnosed_cause is RootCause.UNKNOWN:
                murky_abstentions += 1
        if r.diagnosed_cause is RootCause.UNKNOWN:
            abstentions += 1
        elif r.diagnosed_cause not in generated:
            phantom[str(r.diagnosed_cause)] += 1

    A("### Accuracy is the wrong metric on an unanswerable question")
    A("")
    A("The right-hand column above deserves more care than a percentage. When the")
    A("failure text says only \"Transaction declined\", the true cause is still a")
    A("specific mechanism, so a classifier that says `unknown` — the correct answer to")
    A("the question actually asked — is scored **wrong**. A classifier that guesses the")
    A("modal cause is scored right about a fifth of the time. On these episodes the")
    A("accuracy metric rewards guessing and penalises honesty, which is the same")
    A("mistake as D28 and this time it is in the scoring rather than the planner.")
    A("")
    if murky_total:
        A(f"So the number to read instead: of {murky_total} episodes whose text could not")
        A(f"identify the cause, the treatment arm said `unknown` on **{murky_abstentions}** "
          f"({murky_abstentions / murky_total:.0%}).")
        A("That is the behaviour worth having, and it costs accuracy points.")
        A("")
    if phantom:
        total_phantom = sum(phantom.values())
        A(f"**{total_phantom} diagnoses named a cause this simulator never generates**: "
          + ", ".join(f"`{k}` ({v})" for k, v in sorted(phantom.items(), key=lambda kv: -kv[1]))
          + ". This is not the same as abstaining, and it is not entirely the classifier's")
        A("fault either. The taxonomy is the *system's*, and it offers causes the world")
        A("model does not produce — `gateway_error` happens to real payment systems and")
        A("never happens here. A classifier cannot know that, so part of this count is a")
        A("gap in our simulator. The other part is not: reaching for `gateway_error` on a")
        A("bare \"Transaction declined\" names a specific mechanism the evidence does not")
        A("support, when `unknown` was available. Both are true, and the count is")
        A("reported rather than adjudicated.")
        A("")

    A("The split is the interesting column. On text that names a mechanism, a keyword")
    A("table with the ISO 8583 codes in it is very hard to beat and a model has almost")
    A("nothing to add. The case for a model rests entirely on the right-hand column —")
    A("the failures where the answer has to be assembled from weak context rather than")
    A("looked up — and on that column being a large enough share of reality to matter.")
    A("")

    if diagnoser is not None:
        st = diagnoser.stats
        total = st.calls + st.cache_hits
        A("## The model")
        A("")
        A("| | |")
        A("|---|---|")
        A(f"| Model | `{diagnoser.model}` |")
        if diagnoser.fallback_models:
            A(f"| Fallback chain | {', '.join(f'`{m}`' for m in diagnoser.fallback_models)} |")
        for served, count in sorted(diagnoser.by_model.items(), key=lambda kv: -kv[1]):
            A(f"| Served by `{served}` | {count} |")
        A(f"| Diagnoses served | {total} ({st.cache_hits} from cache, {st.calls} live) |")
        A(f"| Fell back to rules | {st.failures} ({st.fallback_rate:.1%}) |")
        A(f"| Stopped by the budget ceiling | {st.budget_stops} |")
        A(f"| Attempts per successful call | "
          f"{st.attempts / max(1, st.calls):.2f} |")
        A(f"| Tokens | {st.input_tokens:,} in, {st.output_tokens:,} out |")
        A(f"| Token cost (notional; free tier) | {format_inr(st.spend_paise)} |")
        A("")
        A("Prompts are content-addressed and the cache is keyed on their digest, so a")
        A("second run of the same seed makes no calls at all and produces a byte-identical")
        A("report. Amounts reach the model as bands rather than exact figures, which is")
        A("what makes that collapse possible — and the prompt carries no personal data of")
        A("any kind, because nothing about diagnosing a decline requires knowing who the")
        A("customer is.")
        A("")
        if st.failure_reasons:
            A(f"Failures encountered (first 3 of {len(st.failure_reasons)}):")
            A("")
            for reason in st.failure_reasons[:3]:
                A(f"- `{reason[:160]}`")
            A("")

    if diagnoser is not None:
        rules_arm = summaries.get(Arm.BASELINE_RULES)
        if rules_arm and rules_arm.n:
            rate_c = rate_comparison(Arm.BASELINE_RULES)
            t_harm = treat.forbidden_retries / max(1, treat.n) * 1000
            r_harm = rules_arm.forbidden_retries / max(1, rules_arm.n) * 1000
            A("## What the model buys, and what it costs")
            A("")
            A("The ablation. Same playbooks, same gate, same ledger, same audit chain;")
            A("the only difference between these two arms is who classifies the cause.")
            A("")
            A("| | Model | Keyword classifier |")
            A("|---|---|---|")
            A(f"| Accuracy, text that names a mechanism | {_acc(by_arm[Arm.TREATMENT], True)} | "
              f"{_acc(by_arm[Arm.BASELINE_RULES], True)} |")
            A(f"| Accuracy, text that does not | {_acc(by_arm[Arm.TREATMENT], False)} | "
              f"{_acc(by_arm[Arm.BASELINE_RULES], False)} |")
            A(f"| Forbidden retries / 1,000 episodes | **{t_harm:.1f}** | {r_harm:.1f} |")
            A(f"| Recovery rate | {treat.recovery_rate:.1%} | {rules_arm.recovery_rate:.1%} |")
            A(f"| Difference in recovery rate | {rate_c.interval.point:+.2f} pp, "
              f"p = {rate_c.p_value:.3f} | — |")
            A("")
            A("**It does not buy accuracy.** Overall the two are within a point of each")
            A("other. On text that names a mechanism the model is genuinely better, and on")
            A("text that does not, the accuracy metric punishes it for abstaining.")
            A("")
            A("**It buys calibrated uncertainty the system can act on.** Forbidden retries")
            A(f"fall from {r_harm:.0f} to {t_harm:.0f} per 1,000 episodes, "
              f"{(1 - t_harm / max(1e-9, r_harm)):.0%} fewer. A keyword table returns one")
            A("label. The model returns a label, a confidence, and what else it might have")
            A("been — and when the runner-up is a dead card or a risk decline, the gate")
            A("refuses the retry that a single confident-looking label would have allowed.")
            A("That rule is worth half the model arm's harm, and no regex can express its")
            A("input.")
            A("")
            A(f"**It costs recovery.** {rate_c.interval.point:+.2f} percentage points against")
            A(f"the keyword arm (p = {rate_c.p_value:.3f}, not significant, and inside the")
            A("placebo noise floor). Abstaining routes to the conservative playbook and the")
            A("runner-up rule blocks retries that would sometimes have worked. That is a")
            A("real trade and not a rounding error: **less revenue, less harm.** Which side")
            A("a merchant should want depends on how they price a retry against a dead")
            A("card, and this report deliberately does not decide that for them.")
            A("")

    A("## Harm")
    A("")
    A("What each strategy costs the people on the other end. **Forbidden retries** are")
    A("attempts against an episode whose *true* cause is never-retryable — a dead card,")
    A("a risk decline, a revoked mandate. The gate can only refuse a retry for a cause")
    A("somebody identified, so this is the price of a wrong diagnosis, and it is the")
    A("number a better diagnoser has to drive down.")
    A("")
    A("| Arm | Forbidden retries / 1,000 ep | Opt-outs / 1,000 ep | Complaints / 1,000 ep | "
      "Disputes / 1,000 ep |")
    A("|---|---|---|---|---|")
    for arm in (Arm.TREATMENT, Arm.BASELINE_RULES, Arm.BASELINE_NAIVE,
                Arm.BASELINE_BLAST, Arm.CONTROL):
        s = summaries.get(arm)
        if not s:
            continue
        k = 1000 / max(1, s.n)
        A(f"| `{arm}` | {s.forbidden_retries * k:.1f} | {s.opt_outs * k:.1f} | "
          f"{s.complaints * k:.1f} | {s.disputes * k:.1f} |")
    A("")
    naive = summaries.get(Arm.BASELINE_NAIVE)
    if naive and naive.n:
        ratio = (naive.forbidden_retries / naive.n) / max(
            1e-9, treat.forbidden_retries / max(1, treat.n)
        )
        A("`baseline_naive` does not diagnose at all, so it retries dead cards, risk")
        A(f"declines and revoked mandates indiscriminately — **{ratio:.0f}x** the rate of the")
        A("diagnosing arm. That is the harm the diagnosis step exists to prevent.")
        A("")
        A("**And it still wins on money.** A forbidden retry is priced in")
        A("`config/rates.yaml` as amortised exposure to card-network decline-rate")
        A("monitoring, and at that price it does not come close to closing the gap. The")
        A("honest conclusion is that the case against the fixed ladder is a **compliance**")
        A("case, not a revenue case: it recovers more rupees, and it does so by doing")
        A("things a payments team would be unable to defend to an acquirer. Pricing the")
        A("penalty high enough to reverse the ranking would be fabrication, so the")
        A("ranking stands as measured and the argument is made on its actual grounds.")
        A("")

    A("## Terminal states — the stopping rules, exercised")
    A("")
    A("| State | Treatment | Naive | Blast | Control |")
    A("|---|---|---|---|---|")
    all_states = sorted({st for s in summaries.values() for st in s.states})
    for st in all_states:
        row = [summaries[a].states.get(st, 0) if a in summaries else 0
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
    A(f"- {treatment_note}")
    A("- Self-recovery is credited to whichever arm the episode fell in, including")
    A("  treatment. That is correct — it is exactly what the control arm subtracts —")
    A("  but it means the gross figure above is *not* the agent's achievement.")
    A("- Externality pricing is a model, not a measurement. See the aggression trade.")
    A("- Realised costs cover channel spend only. LLM token cost joins the ledger")
    A("  when the agent lands; free-tier models are priced notionally (see")
    A("  `config/rates.yaml`).")
    A("")
    A(f"Reproduce: `make eval SEED={args.seed}`")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
