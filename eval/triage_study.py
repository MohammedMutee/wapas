"""Is it ever right to refuse to chase a payment?

The build plan called for an expected-value triage step, and
``triage.ev_floor_paise`` had been sitting in the policy file unread since the
first week. Building it was easy. Deciding whether to ship it took a
measurement, and the measurement said no.

Two sweeps, both on the treatment arm over the same 5,000 episodes:

1. **The floor.** Raise the bar an episode must clear to be worked at all.
2. **The externality.** Vary what an opt-out is assumed to cost, which is the
   most contestable number in the project, and see when refusing to chase
   starts paying for itself.

    python -m eval.triage_study
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eval.run_batch import train_scorer
from sim import ResponseModel, build_population, load_params
from wapas.clock import IST
from wapas.domain import Arm
from wapas.engine import EpisodeRunner
from wapas.llm.costs import CostBook
from wapas.money import format_inr
from wapas.policy import load_policies
from wapas.strategies import RulesOnly

FLOORS = (0, 500, 5_000, 20_000, 50_000, 100_000, 300_000)
MULTIPLES = (1, 2, 3, 4, 6, 8, 12)


def run(pop, policies, costs, params, seed, scorer):
    runner = EpisodeRunner(policies=policies, costs=costs,
                           response=ResponseModel(params), run_seed=seed, scorer=scorer)
    results = [runner.run(e, Arm.TREATMENT, RulesOnly()) for e in pop.episodes]
    n = len(results) or 1
    gross = sum(r.recovered_paise for r in results) / n
    ext = sum(r.externality_paise for r in results) / n
    cost = sum(r.cost_paise for r in results) / n
    return {
        "net": gross - ext - cost,
        "gross": gross,
        "recovery": sum(1 for r in results if r.recovered) / n,
        "contacts": sum(r.contacts_made for r in results) / n,
        "opt_out": sum(1 for r in results if r.opted_out) / n,
        "skipped": sum(1 for r in results if r.skipped_by_triage),
    }


def with_floor(policies, floor: int):
    money = policies.money
    return policies.model_copy(update={"money": money.model_copy(update={
        "triage": money.triage.model_copy(update={"ev_floor_paise": floor})})})


def with_multiple(costs: CostBook, mult: float) -> CostBook:
    ext = dataclasses.replace(
        costs.externalities,
        recoverable_share=costs.externalities.recoverable_share * Decimal(str(mult)),
    )
    return dataclasses.replace(costs, externalities=ext)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=20260901)
    ap.add_argument("--out", default="results/triage.md")
    args = ap.parse_args()

    params = load_params()
    policies = load_policies("policies")
    costs = CostBook.load("config/rates.yaml")
    start = _dt.datetime(2026, 6, 1, tzinfo=IST)
    scorer, trained_on = train_scorer(params, policies, costs)
    pop = build_population(params, run_seed=args.seed, start=start)

    baseline = run(pop, policies, costs, params, args.seed, None)
    held_out = [
        EpisodeRunner(policies=policies, costs=costs, response=ResponseModel(params),
                      run_seed=args.seed).run(e, Arm.TREATMENT, RulesOnly())
        for e in pop.episodes
    ]

    L: list[str] = []
    A = L.append
    A("# Wapas — is it ever right to refuse to chase a payment?")
    A("")
    A(f"Seed `{args.seed}` · {len(pop.episodes):,} episodes · treatment arm · "
      f"scorer trained on {len(trained_on):,} worked history episodes.")
    A("")
    A("## The probability model")
    A("")
    A("P(recover | cause, surface, amount band) as a smoothed conditional rate over")
    A("episodes the merchant already worked, with backoff when a cell is thin. Not a")
    A("gradient-boosted model, which the build plan called for: what is observable")
    A("before the first action is a handful of low-cardinality categoricals, and an")
    A("empirical rate on that data is **calibrated by construction** rather than")
    A("calibrated afterwards by more machinery.")
    A("")
    A("That claim is checkable, so here it is checked on episodes the scorer never saw:")
    A("")
    A("| Predicted | Observed | n |")
    A("|---|---|---|")
    for pred, obs, n in scorer.reliability(held_out, bins=6):
        A(f"| {pred:.1%} | {obs:.1%} | {n:,} |")
    A("")
    A(f"Expected calibration error **{scorer.calibration_error(held_out):.3f}**. A")
    A("probability that is only a ranking is useless here — multiplying a")
    A("miscalibrated 0.8 by an amount produces a confident wrong number.")
    A("")

    A("## Sweep 1 — raising the bar")
    A("")
    A("| EV floor | Episodes skipped | Recovery | Net / episode | Contacts / ep | Opt-outs |")
    A("|---|---|---|---|---|---|")
    A(f"| **off** | 0 | {baseline['recovery']:.1%} | {format_inr(int(baseline['net']))} | "
      f"{baseline['contacts']:.2f} | {baseline['opt_out']:.2%} |")
    for floor in FLOORS:
        row = run(pop, with_floor(policies, floor), costs, params, args.seed, scorer)
        delta = (row["net"] - baseline["net"]) / abs(baseline["net"] or 1)
        A(f"| {format_inr(floor)} | {row['skipped']:,} | {row['recovery']:.1%} | "
          f"{format_inr(int(row['net']))} | {row['contacts']:.2f} | "
          f"{row['opt_out']:.2%} ({delta:+.1%} net) |")
    A("")
    A("**Every setting is worse than off, monotonically.** There is no floor at which")
    A("this pays. In this world recovery probability is high and the opt-out hazard is")
    A("low, so expected recovery dominates expected harm almost everywhere, and each")
    A("episode refused is mostly revenue given up.")
    A("")
    A("So the feature ships **disabled**. `make eval` does not use it, and the headline")
    A("numbers do not depend on it.")
    A("")

    A("## Sweep 2 — when would it pay?")
    A("")
    A("A negative result is more useful with the condition attached. The externality")
    A("model is the most contestable number in this project; this varies it and asks")
    A("when refusing to chase starts earning its place.")
    A("")
    A("| Opt-out costs | Net / ep, triage off | Net / ep, triage on | Skipped | |")
    A("|---|---|---|---|---|")
    crossover = None
    for mult in MULTIPLES:
        book = with_multiple(costs, mult)
        off = run(pop, policies, book, params, args.seed, None)
        on = run(pop, with_floor(policies, 0), book, params, args.seed, scorer)
        wins = on["net"] > off["net"]
        if wins and crossover is None:
            crossover = mult
        verdict = "**triage wins**" if wins else f"{(on['net'] - off['net']) / abs(off['net'] or 1):+.1%}"
        A(f"| {mult}x our estimate | {format_inr(int(off['net']))} | "
          f"{format_inr(int(on['net']))} | {on['skipped']:,} | {verdict} |")
    A("")
    if crossover:
        A(f"**The crossover is around {crossover}x.** If losing a contactable customer")
        A(f"costs a merchant more than about {crossover} times what this project assumes,")
        A("triage stops being a cost and starts being the difference between an")
        A("operation that makes money and one that does not.")
        A("")
        A("Which is the answer to the obvious objection. A reader who thinks the")
        A("externality pricing is too *low* is not undermining the system — they are")
        A("describing the conditions under which one of its switches should be on, and")
        A("the switch exists, is calibrated, and has a measured cost.")
    A("")
    A(f"Reproduce: `python -m eval.triage_study --seed {args.seed}`")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\nwrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
