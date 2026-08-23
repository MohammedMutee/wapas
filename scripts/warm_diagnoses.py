"""Fill the diagnosis cache for a whole seeded population, concurrently.

``make eval`` runs 5,000 episodes. Amounts are banded and the failure signals
come from a fixed pool, so those collapse to a few hundred distinct prompts —
but a few hundred sequential calls to a free endpoint is still twenty minutes
and a lot of goodwill. This warms them in parallel, once, so every subsequent
evaluation run is a cache read: instant, free, and identical every time.

Failures are reported, never hidden. If the endpoint refuses fifty prompts, the
evaluation will fall back to the rules classifier on those episodes, the report
will show the fallback rate, and the comparison stays honest — a partly warmed
cache produces a partly degraded agent, which is exactly what it should look
like.

    python scripts/warm_diagnoses.py --seed 20260901 --workers 8
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sim import build_population, load_params
from wapas.clock import IST
from wapas.config import settings
from wapas.diagnose import SYSTEM, DiagnosisCache, DiagnosisResponse, LLMDiagnoser
from wapas.diagnose.fleet import FleetView
from wapas.diagnose.history import build_history
from wapas.llm import OpenAICompatProvider, ask_structured
from wapas.llm.costs import CostBook
from wapas.llm.retry import RetryingProvider
from wapas.strategies.base import StrategyContext

HISTORY_SEED = 770777
"""The merchant's resolved past. A different seed from any evaluation run, so
history and evaluation never share an episode."""


def build_provider(cfg):
    return RetryingProvider(
        OpenAICompatProvider(
            base_url=cfg.nvidia_base_url,
            api_key=cfg.nvidia_api_key.get_secret_value(),
            name="nvidia",
        ),
        attempts=3,
        base_delay_s=2.0,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=20260901)
    ap.add_argument("--params", default="sim/params.yaml")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="stop after N new prompts")
    args = ap.parse_args()

    cfg = settings()
    if cfg.nvidia_api_key is None:
        print("no NVIDIA key configured; nothing to warm", file=sys.stderr)
        return 1

    params = load_params(args.params)
    costs = CostBook.load("config/rates.yaml")
    population = build_population(
        params, run_seed=args.seed, start=_dt.datetime(2026, 6, 1, tzinfo=IST)
    )
    cache = DiagnosisCache()
    history = build_history(params, seed=HISTORY_SEED, start=_dt.datetime(2026, 6, 1, tzinfo=IST))
    # The warmer must build exactly the prompts the evaluation will build. It
    # once omitted the fleet view and reported "0 prompts to fetch" for a cache
    # that was missing 46 of them.
    fleet = FleetView.from_episodes(population.episodes)
    probe = LLMDiagnoser(build_provider(cfg), model=cfg.model_reasoning, costs=costs,
                         history=history, fleet=fleet)
    print(f"model: {cfg.model_reasoning}", file=sys.stderr)

    pending: dict[str, str] = {}
    for ep in population.episodes:
        ctx = StrategyContext(
            opened_at=ep.occurred_at, now=ep.occurred_at, surface=ep.surface,
            amount_paise=ep.amount_paise, rail=ep.rail, error_code=ep.error_code,
            error_description=ep.error_description, error_source=ep.error_source,
            error_step=ep.error_step, attempt_no=1,
            is_business=getattr(ep.counterparty, "is_business", False),
            issuer=getattr(ep, "issuer", ""),
        )
        if history.exact(ep.error_description) is not None:
            continue  # answered from history; the model is never asked
        user, digest = probe.prompt_for(ctx)
        if digest not in cache.entries:
            pending.setdefault(digest, user)

    if args.limit:
        pending = dict(list(pending.items())[: args.limit])
    print(f"{len(population.episodes)} episodes -> {len(pending)} prompts to fetch "
          f"({len(cache.entries)} already cached)", file=sys.stderr)
    if not pending:
        return 0

    lock = threading.Lock()
    local = threading.local()
    done = {"ok": 0, "fail": 0}
    failures: list[str] = []
    started = time.monotonic()

    def fetch(item: tuple[str, str]):
        digest, user = item
        if not hasattr(local, "provider"):
            local.provider = build_provider(cfg)
        parsed, response = ask_structured(
            local.provider, model=cfg.model_reasoning, system=SYSTEM, user=user,
            schema_model=DiagnosisResponse, max_tokens=1400,
        )
        return digest, parsed, response

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch, item): item[0] for item in pending.items()}
        for future in as_completed(futures):
            try:
                digest, parsed, _ = future.result()
            except Exception as exc:
                with lock:
                    done["fail"] += 1
                    failures.append(f"{type(exc).__name__}: {exc}")
                continue
            with lock:
                cache.put(digest, parsed.model_dump(mode="json"))
                done["ok"] += 1
                if done["ok"] % 25 == 0:
                    cache.save()
                    rate = done["ok"] / max(1e-9, time.monotonic() - started)
                    print(f"  {done['ok']}/{len(pending)} ok, {done['fail']} failed, "
                          f"{rate:.1f}/s", file=sys.stderr)

    cache.save()
    elapsed = time.monotonic() - started
    print(f"\nwarmed {done['ok']} prompts in {elapsed:.0f}s, {done['fail']} failed",
          file=sys.stderr)
    for reason in failures[:5]:
        print(f"  {reason[:180]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
