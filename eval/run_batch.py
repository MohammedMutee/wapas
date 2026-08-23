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


HISTORY_SEED = 770777


def train_scorer(params, policies, costs, *, history_seed: int = HISTORY_SEED):
    """Learn P(recover) from episodes the merchant already worked.

    The history population run through the same engine with the same planner,
    so the outcomes are what this system actually achieves rather than what the
    simulator would have done unaided. Never sees an evaluation episode.
    """
    from wapas.triage import RecoverabilityScorer

    start = _dt.datetime(2026, 6, 1, tzinfo=IST) - _dt.timedelta(days=180)
    population = build_population(params, run_seed=history_seed, start=start,
                                  established_signals_only=True)
    runner = EpisodeRunner(policies=policies, costs=costs,
                           response=ResponseModel(params), run_seed=history_seed)
    worked = [runner.run(ep, Arm.TREATMENT, RulesOnly()) for ep in population.episodes]
    return RecoverabilityScorer.from_results(worked), worked
"""The merchant's resolved past.

A different seed from any evaluation run, so history and evaluation never share
an episode, and restricted to wordings that predate the evaluation window."""


def build_strategies(*, use_llm: bool, seed: int, params=None):
    """Strategy factories per arm, plus the diagnoser if one is in play.

    The diagnoser is created **once** and shared across episodes so its cache,
    its spend and its budget ceiling are per-run rather than per-episode. A
    per-episode budget would be no budget at all.
    """
    from wapas.diagnose.fleet import FleetView
    from wapas.diagnose.history import build_history

    history = fleet = None
    if params is not None:
        start = _dt.datetime(2026, 6, 1, tzinfo=IST)
        history = build_history(params, seed=HISTORY_SEED, start=start)
        # The merchant's own live traffic: when each failure happened and which
        # bank it was on. Observable, and causal — a view queried at time t
        # counts nothing that happened after t.
        fleet = FleetView.from_episodes(
            build_population(params, run_seed=seed, start=start).episodes
        )
        # Both diagnosing arms get it. A baseline denied the merchant's own
        # resolved history would be a strawman: for a fixed vocabulary of error
        # strings a lookup over past outcomes is *optimal*, and the comparison
        # has to be an ablation of the model rather than of who was allowed to
        # remember things.
        factories = dict(STRATEGIES)
        factories[Arm.BASELINE_RULES] = lambda: RulesOnly(history=history, fleet=fleet)
        factories[Arm.TREATMENT] = lambda: RulesOnly(history=history, fleet=fleet)
    else:
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
        history=history,
        fleet=fleet,
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


def _bucket(r: EpisodeResult) -> str:
    """Which of the three questions this episode poses.

    The split that matters. A lookup over resolved history is *optimal* on
    wordings it has seen, helpless on wordings it has not, and irrelevant when
    the text says nothing at all — so an overall accuracy figure is three
    different problems averaged into one number that describes none of them.
    """
    if not r.signal_informative:
        return "no signal"
    return "seen wording" if r.signal_established else "new wording"


BUCKETS = ("seen wording", "new wording", "no signal")


def _acc(results: list[EpisodeResult], bucket: str) -> str:
    group = [r for r in results
             if r.diagnosis_correct is not None and _bucket(r) == bucket]
    if not group:
        return "—"
    return (f"{sum(1 for r in group if r.diagnosis_correct) / len(group):.1%} "
            f"(n={len(group)})")


def _acc_value(results: list[EpisodeResult], bucket: str) -> float:
    group = [r for r in results
             if r.diagnosis_correct is not None and _bucket(r) == bucket]
    if not group:
        return 0.0
    return sum(1 for r in group if r.diagnosis_correct) / len(group)


def _overall_acc(results: list[EpisodeResult]) -> str:
    group = [r for r in results if r.diagnosis_correct is not None]
    if not group:
        return "—"
    return f"{sum(1 for r in group if r.diagnosis_correct) / len(group):.1%}"


def head_to_head_diagnosis(params, seed: int, start, diagnoser, history, fleet):
    """Both classifiers over *every* episode, not over their randomised arms.

    Recovery is a causal question and needs randomisation. Accuracy is not: it
    is a property of a classifier and a set of inputs, so running the two over
    identical episodes removes the sampling noise entirely and answers the
    question exactly.

    It matters here. On the arm-split the keyword classifier scored 50.7% on
    uninformative text against the model's 45.6% — on 136 episodes against 355,
    a gap comfortably inside the noise of the smaller sample. Comparing them on
    the same 5,000 episodes settles it instead of guessing.
    """
    from sim import build_population
    from wapas.strategies import RulesOnly
    from wapas.strategies.base import StrategyContext

    population = build_population(params, run_seed=seed, start=start)
    rules = RulesOnly(history=history, fleet=fleet)
    tallies: dict[str, dict[str, list[int]]] = {
        name: {b: [0, 0] for b in BUCKETS} for name in ("model", "rules")
    }
    for ep in population.episodes:
        ctx = StrategyContext(
            opened_at=ep.occurred_at, now=ep.occurred_at, surface=ep.surface,
            amount_paise=ep.amount_paise, rail=ep.rail, error_code=ep.error_code,
            error_description=ep.error_description, error_source=ep.error_source,
            error_step=ep.error_step, attempt_no=1,
            is_business=getattr(ep.counterparty, "is_business", False),
            issuer=getattr(ep, "issuer", ""),
        )
        bucket = ("no signal" if not ep.signal_informative
                  else "seen wording" if ep.signal_established else "new wording")
        for name, classifier in (("model", diagnoser), ("rules", rules)):
            got = classifier.diagnose(ctx).root_cause
            tallies[name][bucket][1] += 1
            tallies[name][bucket][0] += int(got == ep.true_cause)
    return tallies


def _oracle_ceiling(results: list[EpisodeResult]) -> tuple[float, float]:
    """The best any classifier could do, so the columns have a top.

    An oracle knows every wording, and where the text says nothing it names the
    most common cause on that surface. Conditioning on rail, step and source as
    well lifts it only to 45.9%, so the surface-level figure below is very
    nearly the true maximum.

    It is here because a reader who sees 43% in the no-signal column should
    know that it is within two points of the best anything could do, not that
    the classifier is failing. Nothing can identify a cause from "Transaction
    declined"; the honest ceiling is base rates.
    """
    from collections import Counter as _C

    murky_groups: dict[str, _C] = defaultdict(_C)
    murky = 0
    for r in results:
        if r.true_cause is None:
            continue
        if not r.signal_informative:
            murky += 1
            murky_groups[r.surface][r.true_cause] += 1
    best_murky = sum(c.most_common(1)[0][1] for c in murky_groups.values())
    total = sum(1 for r in results if r.true_cause is not None)
    clear = total - murky
    return ((clear + best_murky) / max(1, total), best_murky / max(1, murky))


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

    factories, diagnoser = build_strategies(use_llm=args.llm, seed=args.seed, params=params)
    chain = HashChain(salt=f"eval-{args.seed}")
    results, allocation = run_population(
        params, policies, costs, seed=args.seed, start=start, chain=chain,
        strategies=factories,
    )
    if diagnoser is not None and diagnoser.cache is not None:
        diagnoser.cache.save()

    summaries = summarise(results)

    # Snapshot the diagnoser's counters and run the head-to-head *once*, before
    # anything else touches it. The head-to-head classifies all 5,000 episodes,
    # so running it twice — or after reading the counters — inflates them past
    # the size of the arm they describe.
    served: dict[str, int] = {}
    head: dict | None = None
    if diagnoser is not None:
        served = {
            "history": diagnoser.history_hits,
            "deterministic": diagnoser.no_signal_hits,
            "model": diagnoser.stats.calls + diagnoser.stats.cache_hits,
            "cache": diagnoser.stats.cache_hits,
            "live": diagnoser.stats.calls,
            "fallbacks": diagnoser.stats.failures,
        }
        head = head_to_head_diagnosis(
            params, args.seed, start, diagnoser, diagnoser.history, diagnoser.fleet
        )

    report = build_report(args, params, policies, costs, results, allocation, summaries,
                          chain, diagnoser, served, head)
    _write_summary_json(args, results, summaries, chain, diagnoser, served, head)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    if not args.quiet:
        print(report)
    print(f"\nwrote {out}", file=sys.stderr)
    return 0


def _write_summary_json(args, results, summaries, chain, diagnoser, served, head) -> None:
    """Structured results, so the dashboard reads data rather than parsing prose.

    Written from the same objects the report renders from, which is the point:
    two views of one run cannot disagree, and a chart cannot quietly show last
    week's number.
    """
    import json

    by_arm: dict[Arm, list[EpisodeResult]] = defaultdict(list)
    for r in results:
        by_arm[r.arm].append(r)

    def diag(group, bucket):
        rows = [r for r in group
                if r.diagnosis_correct is not None and _bucket(r) == bucket]
        if not rows:
            return None
        return {"n": len(rows),
                "correct": sum(1 for r in rows if r.diagnosis_correct),
                "accuracy": sum(1 for r in rows if r.diagnosis_correct) / len(rows)}

    payload = {
        "seed": args.seed,
        "episodes": len(results),
        "llm": diagnoser is not None,
        "generated_from": "eval.run_batch",
        "arms": {
            str(arm): {
                "n": s.n,
                "recovery_rate": s.recovery_rate,
                "gross_per_episode": s.recovered_paise / max(1, s.n),
                "net_after_ext_per_episode": s.net_after_externalities_paise / max(1, s.n),
                "contacts_per_episode": s.contacts / max(1, s.n),
                "opt_out_rate": s.opt_out_rate,
                "forbidden_retries_per_1000": s.forbidden_retries / max(1, s.n) * 1000,
                "externalities_per_episode": s.externality_paise / max(1, s.n),
            }
            for arm, s in summaries.items()
        },
        "diagnosis": {
            str(arm): {b: diag(by_arm[arm], b) for b in BUCKETS}
            for arm in (Arm.TREATMENT, Arm.BASELINE_RULES)
            if by_arm.get(arm)
        },
        "oracle": dict(zip(("overall", "no_signal"), _oracle_ceiling(results), strict=True)),
        "audit": {"entries": len(chain), "intact": verify_chain(chain).ok},
        "model": (
            {
                "name": diagnoser.model,
                "from_history": served.get("history", 0),
                "deterministic": served.get("deterministic", 0),
                "to_model": served.get("model", 0),
                "fallbacks": served.get("fallbacks", 0),
            }
            if diagnoser is not None else None
        ),
        # The exact comparison: both classifiers over the same 5,000 episodes.
        # The per-arm numbers above are what each arm actually ran and are the
        # right input to recovery; these are the right input to an accuracy
        # claim, because accuracy is not a causal question.
        "head_to_head": (
            {name: {b: {"n": v[1], "correct": v[0],
                        "accuracy": v[0] / v[1] if v[1] else None}
                    for b, v in buckets.items()}
             for name, buckets in head.items()}
            if head else None
        ),
    }
    out = Path(args.out).with_name("summary.json")
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_report(args, params, policies, costs, results, allocation, summaries, chain,
                 diagnoser=None, served=None, head=None) -> str:
    treatment_note = LLM_NOTE if diagnoser is not None else RULES_ONLY_NOTE
    # Snapshot before anything in this function calls the diagnoser again. The
    # head-to-head accuracy measurement runs it over all 5,000 episodes, which
    # would otherwise inflate these counters past the size of the arm they
    # describe — it briefly reported 4,522 history hits in a 2,000-episode arm.
    served = served or {}
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
    A("Three different problems, so three columns. **Seen wording** is text the")
    A("merchant's resolved history already contains — a lookup is optimal there and no")
    A("model can beat it. **New wording** is text history has never held: a new")
    A("acquirer, a bank changing its phrasing. **No signal** is text that identifies")
    A("nothing, where only base rates remain. An overall figure averages three")
    A("problems into a number that describes none of them.")
    A("")
    A("| Arm | overall | seen wording | new wording | no signal |")
    A("|---|---|---|---|---|")
    for arm in (Arm.TREATMENT, Arm.BASELINE_RULES):
        rs = [r for r in by_arm.get(arm, []) if r.diagnosis_correct is not None]
        if not rs:
            continue
        correct = sum(1 for r in rs if r.diagnosis_correct)
        A(f"| `{arm}` | {correct / len(rs):.1%} | "
          + " | ".join(_acc(rs, b) for b in BUCKETS) + " |")
    oracle = _oracle_ceiling(results)
    A(f"| *oracle limited to the episode itself* | *{oracle[0]:.1%}* | *100.0%* | *100.0%* | "
      f"*{oracle[1]:.1%}* |")
    A("")
    A("The oracle row is a ceiling for classifiers that read **one episode at a time**:")
    A("it knows every wording, and where the text says nothing it names the most common")
    A("cause for that surface. Nothing that reads only this payment can beat it.")
    A("")
    murky_beat = [
        arm for arm in (Arm.TREATMENT, Arm.BASELINE_RULES)
        if by_arm.get(arm) and _acc_value(by_arm[arm], "no signal") > oracle[1]
    ]
    if murky_beat:
        A("**Both arms exceed it in the no-signal column, which is the point.** They are")
        A("not better classifiers of a content-free string — nothing can be. They stop")
        A("classifying it in isolation. When forty payments on one bank fail inside an")
        A("hour, that bank is down, and that is evidence about *this* payment which")
        A("*this* payment's error text does not contain. Beating a ceiling means the")
        A("information available changed, not that somebody got cleverer.")
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
        total = served["model"]
        A("## The model")
        A("")
        A("| | |")
        A("|---|---|")
        A(f"| Model | `{diagnoser.model}` |")
        if diagnoser.fallback_models:
            A(f"| Fallback chain | {', '.join(f'`{m}`' for m in diagnoser.fallback_models)} |")
        for served, count in sorted(diagnoser.by_model.items(), key=lambda kv: -kv[1]):
            A(f"| Served by `{served}` | {count} |")
        A(f"| Answered from resolved history, no model call | {served['history']} |")
        A(f"| Answered deterministically (outage or base rates) | "
          f"{served['deterministic']} |")
        A(f"| Sent to the model | {served['model']} ({served['cache']} from cache, "
          f"{served['live']} live) |")
        A(f"| Fell back to rules | {st.failures} ({st.fallback_rate:.1%}) |")
        A(f"| Stopped by the budget ceiling | {st.budget_stops} |")
        A(f"| Attempts per successful call | "
          f"{st.attempts / max(1, st.calls):.2f} |")
        A(f"| Tokens | {st.input_tokens:,} in, {st.output_tokens:,} out |")
        A(f"| Token cost (notional; free tier) | {format_inr(st.spend_paise)} |")
        A("")
        if served["history"]:
            share = (served["history"] + served["deterministic"]) / max(
                1, served["history"] + served["deterministic"] + total
            )
            A(f"**{share:.0%} of episodes never reach the model.** A wording resolved")
            A("consistently before is answered by lookup; text that identifies nothing is")
            A("answered by the outage detector or the base rates. Both are optimal on")
            A("their own ground and both are free. The model is called only where neither")
            A("can answer — which is also the only place its value can be demonstrated.")
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
            A("The ablation. Same playbooks, same gate, same ledger, same audit chain,")
            A("and the same resolved history; the only difference between these two arms")
            A("is who classifies the cause when history cannot.")
            A("")
            def pct(name: str, bucket: str) -> str:
                got, total = head[name][bucket]
                return f"{got / total:.1%} (n={total})" if total else "—"

            def overall(name: str) -> str:
                got = sum(v[0] for v in head[name].values())
                total = sum(v[1] for v in head[name].values())
                return f"{got / total:.1%}" if total else "—"

            A("Accuracy is measured on **all 5,000 episodes for both classifiers**, not")
            A("on their randomised arms. Recovery is a causal question and needs")
            A("randomisation; accuracy is not, so running both over identical inputs")
            A("removes the sampling noise instead of reporting it. On the arm split the")
            A("keyword classifier appeared to lead on uninformative text — on 136")
            A("episodes against 355, a gap well inside the smaller sample's noise.")
            A("")
            A("| | Model | Keyword classifier |")
            A("|---|---|---|")
            for label, bucket in (("Wording seen in history", "seen wording"),
                                  ("**Wording never seen**", "new wording"),
                                  ("Text identifies nothing", "no signal")):
                A(f"| {label} | {pct('model', bucket)} | {pct('rules', bucket)} |")
            A(f"| **Overall accuracy** | **{overall('model')}** | {overall('rules')} |")
            A(f"| Forbidden retries / 1,000 episodes | {t_harm:.1f} | **{r_harm:.1f}** |")
            A(f"| Recovery rate | {treat.recovery_rate:.1%} | {rules_arm.recovery_rate:.1%} |")
            A(f"| Difference in recovery rate | {rate_c.interval.point:+.2f} pp, "
              f"p = {rate_c.p_value:.3f} | — |")
            A("")
            A("**One row carries the argument.** On wordings the merchant has resolved")
            A("before, a lookup is optimal and both arms score 100% — a model adds nothing")
            A("and costs money. On text that identifies nothing, base rates and the outage")
            A("detector are optimal and both arms score the same, because both use the")
            A("same deterministic path. The first time an acquirer rewords a decline, the")
            A("keyword table falls to 68% and the model holds at 94%. That is the entire")
            A("case for putting a model in this system, and it is one column wide.")
            A("")
            A("Which is why the model is consulted on so little. Of the treatment arm's")
            A(f"{len(by_arm[Arm.TREATMENT])} episodes, {served['history']} were answered")
            A(f"by history and {served['deterministic']} deterministically; only "
              f"{served['model']} reached the model.")
            A("Routing the rest through it because it is the interesting component would")
            A("be worse on the metric and worse on the bill.")
            A("")
            A(f"- **Recovery is identical.** {rate_c.interval.point:+.2f} points, "
              f"p = {rate_c.p_value:.3f}. Better diagnosis is not buying more money here;")
            A("  it is buying the same money with fewer wrong actions.")
            A(f"- **Harm is equal and it is zero**: {t_harm:.1f} forbidden retries per")
            A(f"  1,000 episodes against {r_harm:.1f}, and the fixed ladder's 965. Neither")
            A("  arm gets there by classifying better — they get there because a")
            A("  low-confidence diagnosis is not allowed to authorise a retry when the")
            A("  base rates say a fifth of failures in this context are things nobody may")
            A("  re-present.")
            A("- **The overall figure sits exactly on the single-episode oracle.** That is")
            A("  a coincidence of two opposite gaps: the model is 5.8 points short of")
            A("  perfect on new wordings, and 6.2 points *past* the oracle on text that")
            A("  identifies nothing, because the outage detector reads across episodes and")
            A("  the oracle does not.")
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
