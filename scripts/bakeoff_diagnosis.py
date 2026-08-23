"""Choose the diagnosis model on evidence, at a sample size that can support it.

The first bake-off (D13) ran **three** hand-written cases. It picked
``openai/gpt-oss-120b``, and it was right to prefer measurement over the
marketing page — but three cases cannot separate two models, and one of its
conclusions had already been overturned once by a second run.

This one draws labelled cases from the simulator, where ground truth is known
by construction, stratified across every root cause so a model cannot score
well by being good at the common ones. It measures four things, because
accuracy alone has repeatedly picked the wrong model here:

**Accuracy** — against the true cause.

**Calibration** — mean confidence when right against mean confidence when
wrong. A model that is 0.95 confident on both is unusable regardless of its
accuracy, because the planner has no way to know when to be careful.

**Harm** — how often it names a retryable cause when the truth is one of the
never-retry causes. That is the misclassification that actually hurts someone,
and it is not symmetric with the reverse error.

**Usability** — latency and how often the call simply fails. A model that is
2% more accurate and times out on one call in five is the worse choice.

    python scripts/bakeoff_diagnosis.py --cases 40 --workers 6
"""

from __future__ import annotations

import argparse
import datetime as _dt
import statistics
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sim import build_population, load_params
from wapas.clock import IST
from wapas.config import settings
from wapas.diagnose import SYSTEM, DiagnosisResponse
from wapas.diagnose.prompt import build_user_prompt
from wapas.domain import NEVER_RETRY, RootCause
from wapas.llm import OpenAICompatProvider, ask_structured
from wapas.llm.retry import RetryingProvider
from wapas.strategies import RulesOnly
from wapas.strategies.base import StrategyContext

CANDIDATES = (
    "openai/gpt-oss-120b",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
)


@dataclass
class Scorecard:
    model: str
    correct: int = 0
    total: int = 0
    failures: int = 0
    harmful: int = 0
    """Called a never-retryable cause retryable. The error that costs someone."""
    clear_right: int = 0
    clear_total: int = 0
    murky_right: int = 0
    murky_total: int = 0
    latencies: list[float] = field(default_factory=list)
    confidence_right: list[float] = field(default_factory=list)
    confidence_wrong: list[float] = field(default_factory=list)
    confusion: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def median_latency(self) -> float:
        return statistics.median(self.latencies) if self.latencies else 0.0

    @property
    def separation(self) -> float:
        """Confidence when right minus confidence when wrong. Higher is usable."""
        if not self.confidence_right or not self.confidence_wrong:
            return 0.0
        return statistics.mean(self.confidence_right) - statistics.mean(self.confidence_wrong)


def sample_cases(params, seed: int, per_cause: int):
    """Labelled cases, stratified across causes so the tail is not ignored."""
    population = build_population(
        params, run_seed=seed, start=_dt.datetime(2026, 6, 1, tzinfo=IST)
    )
    buckets: dict[RootCause, list] = defaultdict(list)
    for ep in population.episodes:
        if len(buckets[ep.true_cause]) < per_cause:
            buckets[ep.true_cause].append(ep)
    return [ep for eps in buckets.values() for ep in eps]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=771001)
    ap.add_argument("--per-cause", type=int, default=4)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--models", nargs="*", default=list(CANDIDATES))
    ap.add_argument("--out", default="results/model_bakeoff.md")
    args = ap.parse_args()

    cfg = settings()
    if cfg.nvidia_api_key is None:
        print("no NVIDIA key configured", file=sys.stderr)
        return 1

    params = load_params()
    cases = sample_cases(params, args.seed, args.per_cause)
    print(f"{len(cases)} labelled cases across "
          f"{len({c.true_cause for c in cases})} causes", file=sys.stderr)

    prompts = []
    for ep in cases:
        prompts.append((ep.true_cause, build_user_prompt(
            surface=ep.surface, rail=ep.rail, error_code=ep.error_code,
            error_description=ep.error_description, error_source=ep.error_source,
            error_step=ep.error_step, amount_paise=ep.amount_paise,
            is_business=getattr(ep.counterparty, "is_business", False),
        ), ep))

    rules = _score_rules(prompts)
    cards = [rules]
    local = threading.local()
    lock = threading.Lock()

    for model in args.models:
        card = Scorecard(model=model)

        def one(item, model=model, card=card):
            truth, user, _ = item
            if not hasattr(local, "provider"):
                local.provider = RetryingProvider(
                    OpenAICompatProvider(
                        base_url=cfg.nvidia_base_url,
                        api_key=cfg.nvidia_api_key.get_secret_value(), name="nvidia",
                    ),
                    attempts=2, base_delay_s=2.0,
                )
            started = time.monotonic()
            try:
                parsed, _ = ask_structured(
                    local.provider, model=model, system=SYSTEM, user=user,
                    schema_model=DiagnosisResponse, max_tokens=1200,
                )
            except Exception:
                with lock:
                    card.total += 1
                    card.failures += 1
                return
            elapsed = time.monotonic() - started
            with lock:
                _record(card, truth, parsed.root_cause, parsed.confidence, elapsed,
                        informative=getattr(item[2], "signal_informative", True))

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(one, prompts))
        cards.append(card)
        print(f"  {model:42} acc {card.accuracy:5.1%}  fail {card.failures:3}  "
              f"p50 {card.median_latency:5.1f}s", file=sys.stderr)

    report = render(args, cards, prompts)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nwrote {out}", file=sys.stderr)
    return 0


def _record(card: Scorecard, truth, guess, confidence: float, elapsed: float,
            informative: bool = True) -> None:
    card.total += 1
    if informative:
        card.clear_total += 1
        card.clear_right += int(guess == truth)
    else:
        card.murky_total += 1
        card.murky_right += int(guess == truth)
    card.latencies.append(elapsed)
    card.confusion[(str(truth), str(guess))] += 1
    if guess == truth:
        card.correct += 1
        card.confidence_right.append(confidence)
    else:
        card.confidence_wrong.append(confidence)
        if truth in NEVER_RETRY and guess not in NEVER_RETRY:
            card.harmful += 1


def _score_rules(prompts) -> Scorecard:
    """The keyword classifier on the same cases. The bar, not a courtesy."""
    card = Scorecard(model="rules_only (no model)")
    classifier = RulesOnly()
    for truth, _user, ep in prompts:
        ctx = StrategyContext(
            opened_at=ep.occurred_at, now=ep.occurred_at, surface=ep.surface,
            amount_paise=ep.amount_paise, rail=ep.rail, error_code=ep.error_code,
            error_description=ep.error_description, error_source=ep.error_source,
            error_step=ep.error_step, attempt_no=1,
            is_business=getattr(ep.counterparty, "is_business", False),
        )
        d = classifier.diagnose(ctx)
        _record(card, truth, d.root_cause, d.confidence, 0.0,
                informative=getattr(ep, "signal_informative", True))
    return card


def render(args, cards: list[Scorecard], prompts) -> str:
    L: list[str] = []
    A = L.append
    A("# Wapas — diagnosis model bake-off")
    A("")
    A(f"{len(prompts)} labelled cases from seed `{args.seed}`, "
      f"{args.per_cause} per root cause, PROMPTED mode, ground truth from the simulator.")
    A("")
    A("Supersedes the three-case probe recorded in `DECISIONS.md` D13. Three cases")
    A("cannot separate two models; this is small but it is stratified across every")
    A("cause, so a model cannot score well by handling only the common ones.")
    A("")
    A("| Model | Accuracy | On informative text | On uninformative | Harmful errors | "
      "Failed calls | p50 latency | Confidence gap |")
    A("|---|---|---|---|---|---|---|---|")
    for card in cards:
        latency = "—" if card.median_latency == 0 else f"{card.median_latency:.1f}s"
        clear = (f"{card.clear_right / card.clear_total:.1%}" if card.clear_total else "—")
        murky = (f"{card.murky_right / card.murky_total:.1%}" if card.murky_total else "—")
        A(f"| `{card.model}` | {card.accuracy:.1%} | {clear} | {murky} | {card.harmful} | "
          f"{card.failures} | {latency} | {card.separation:+.2f} |")
    A("")

    baseline = next((c for c in cards if c.model.startswith("rules_only")), None)
    best = max((c for c in cards if not c.model.startswith("rules_only")),
               key=lambda c: c.accuracy, default=None)
    if baseline and best and baseline.total:
        gap_cases = best.correct - baseline.correct
        A("### Is the gap real?")
        A("")
        A(f"The best model is **{gap_cases} cases** ahead of the keyword classifier out")
        A(f"of {baseline.total} — {best.accuracy - baseline.accuracy:+.1%}. On a sample")
        A("this size that is not a difference anyone should act on. A run of 52 cases")
        A("can separate a working model from a broken one, which is what it was for; it")
        A("cannot separate two models that both roughly work, and it cannot establish")
        A("that a model beats a good keyword table.")
        A("")
        A("The number that decides that question is the **on uninformative** column,")
        A("measured at evaluation scale in `results/report.md`, where the treatment arm")
        A("classifies two thousand episodes rather than fifty-two. Selection is what")
        A("this file is for. Proof is not.")
        A("")
    A("**Harmful errors** are cases where the true cause is never-retryable — a dead")
    A("card, a risk decline, a revoked mandate — and the model named something")
    A("retryable. The planner acts on that, so it is the error that reaches a real")
    A("person. It is not symmetric with the reverse mistake and is not averaged into")
    A("accuracy.")
    A("")
    A("**Confidence gap** is mean confidence when correct minus mean confidence when")
    A("wrong. A model with high accuracy and a gap near zero is still hard to use,")
    A("because nothing downstream can tell when to be careful.")
    A("")

    for card in cards:
        wrong = {k: v for k, v in card.confusion.items() if k[0] != k[1]}
        if not wrong:
            continue
        A(f"### Where `{card.model}` goes wrong")
        A("")
        A("| True cause | Called it | n |")
        A("|---|---|---|")
        for (truth, guess), n in sorted(wrong.items(), key=lambda kv: -kv[1])[:8]:
            A(f"| `{truth}` | `{guess}` | {n} |")
        A("")
    A(f"Reproduce: `python scripts/bakeoff_diagnosis.py --seed {args.seed} "
      f"--per-cause {args.per_cause}`")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
