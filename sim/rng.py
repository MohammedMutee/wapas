"""Deterministic random draws.

Every random value in the simulation derives from a single run seed via a
*hierarchical* scheme: a stream is identified by a label and an index, and its
seed is derived by hashing them together with the run seed. Two consequences,
both necessary:

* **Reproducibility.** The same run seed always produces the same world.
* **Independence.** Adding a draw to one episode does not shift the values
  drawn by any other episode. A single shared generator would make every
  result depend on iteration order, so a small code change would silently
  change every number in the report.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Sequence
from typing import Any

from .params import Categorical, Dist


def derive_seed(run_seed: int, *parts: object) -> int:
    """Stable child seed from a run seed and any labelling parts."""
    material = "|".join([str(run_seed), *(str(p) for p in parts)])
    return int.from_bytes(hashlib.sha256(material.encode()).digest()[:8], "big")


class Rng:
    """A named, independent stream of draws."""

    def __init__(self, run_seed: int, *parts: object) -> None:
        self.seed = derive_seed(run_seed, *parts)
        self._r = random.Random(self.seed)

    def child(self, *parts: object) -> Rng:
        return Rng(self.seed, *parts)

    # ── primitives ───────────────────────────────────────────────────────────

    def uniform(self, lo: float = 0.0, hi: float = 1.0) -> float:
        return self._r.uniform(lo, hi)

    def randint(self, lo: int, hi: int) -> int:
        return self._r.randint(lo, hi)

    def chance(self, p: float) -> bool:
        return self._r.random() < p

    def choice(self, items: Sequence[Any]) -> Any:
        return self._r.choice(list(items))

    def categorical(self, spec: Categorical) -> Any:
        return self._r.choices(list(spec.values), weights=list(spec.weights), k=1)[0]

    def weighted(self, mapping: dict[str, float]) -> str:
        keys = list(mapping)
        return self._r.choices(keys, weights=[mapping[k] for k in keys], k=1)[0]

    def draw(self, spec: Dist) -> float:
        match spec.dist:
            case "beta":
                return self._r.betavariate(spec.a or 1.0, spec.b or 1.0)
            case "poisson":
                return float(self._poisson(spec.lam or 1.0))
            case "lognormal":
                return self._r.lognormvariate(spec.mu or 0.0, spec.sigma or 1.0)
            case "uniform":
                return self._r.uniform(spec.a or 0.0, spec.b or 1.0)
            case _:  # pragma: no cover - validated at load
                raise ValueError(f"unknown distribution {spec.dist!r}")

    def _poisson(self, lam: float) -> int:
        """Knuth's method. Adequate for the small lambdas used here."""
        limit, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self._r.random()
            if p <= limit:
                return k
            k += 1


def logistic(log_odds: float) -> float:
    """Numerically stable inverse logit."""
    if log_odds >= 0:
        z = math.exp(-log_odds)
        return 1.0 / (1.0 + z)
    z = math.exp(log_odds)
    return z / (1.0 + z)
