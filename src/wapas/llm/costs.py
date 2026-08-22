"""Token cost accounting.

The headline metric is *net* incremental recovery, so every model call has to
carry a price. Two wrinkles this module handles honestly:

**Free credits still cost something.** NVIDIA's developer tier serves open
models at no charge, which would make the cost line zero and the net-recovery
metric meaningless. We therefore book a **notional** price: the published list
rate for comparable hosted inference of a model that size. Every such rate is
flagged ``notional: true`` in ``config/rates.yaml`` and the report labels the
column accordingly. Understating cost would flatter our own numbers, which is
exactly the failure mode the evaluation design exists to prevent.

**FX drifts.** The USD→INR rate is pinned in the rate card with the date it was
pinned. A floating rate would make yesterday's report irreproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from ..money import Paise
from .base import Usage

CACHE_READ_DISCOUNT = Decimal("0.10")
"""Cached input tokens bill at 10% of the input rate where a provider supports it."""


@dataclass(frozen=True, slots=True)
class ModelRate:
    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal
    notional: bool = False
    note: str = ""


@dataclass(frozen=True, slots=True)
class CostBook:
    """Loaded rate card. Immutable, versioned, and stamped onto every report."""

    version: str
    pinned_on: str
    usd_inr: Decimal
    models: dict[str, ModelRate]
    channels: dict[str, Paise]

    @classmethod
    def load(cls, path: str | Path = "config/rates.yaml") -> CostBook:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        models = {
            name: ModelRate(
                input_usd_per_mtok=Decimal(str(v["input"])),
                output_usd_per_mtok=Decimal(str(v["output"])),
                notional=bool(v.get("notional", False)),
                note=str(v.get("note", "")),
            )
            for name, v in (data.get("models") or {}).items()
        }
        channels: dict[str, Paise] = {}
        for name, v in (data.get("channels") or {}).items():
            if "paise_per_unit" in v:
                channels[name] = Paise(int(v["paise_per_unit"]))
            else:
                per_min = Decimal(str(v["paise_per_minute"]))
                mins = Decimal(str(v.get("assumed_minutes", 1)))
                channels[name] = Paise(int((per_min * mins).to_integral_value()))
        return cls(
            version=str(data["version"]),
            pinned_on=str(data["pinned_on"]),
            usd_inr=Decimal(str(data["fx"]["usd_inr"])),
            models=models,
            channels=channels,
        )

    def model_rate(self, model: str) -> ModelRate:
        if model not in self.models:
            raise KeyError(
                f"no rate for model {model!r}. Add it to config/rates.yaml — an "
                f"unpriced model would silently understate the cost line."
            )
        return self.models[model]

    def any_notional(self) -> list[str]:
        """Models priced notionally. Surfaced in the report so nobody is misled."""
        return sorted(n for n, r in self.models.items() if r.notional)


def cost_usd(usage: Usage, rate: ModelRate, *, batch: bool = False) -> Decimal:
    """USD cost of one call.

    ``cached`` input bills at a discount; ``batch`` halves everything where the
    provider offers an asynchronous batch tier.
    """
    uncached = max(0, usage.input_tokens - usage.cached_input_tokens)
    total = (
        Decimal(uncached) / 1_000_000 * rate.input_usd_per_mtok
        + Decimal(usage.cached_input_tokens) / 1_000_000 * rate.input_usd_per_mtok * CACHE_READ_DISCOUNT
        + Decimal(usage.output_tokens) / 1_000_000 * rate.output_usd_per_mtok
    )
    return total / 2 if batch else total


def cost_paise(usage: Usage, model: str, book: CostBook, *, batch: bool = False) -> Paise:
    """Cost of one call in integer paise, rounded up.

    Rounding **up** is deliberate: when the cost line is uncertain, err towards
    reporting a smaller net recovery rather than a larger one.
    """
    usd = cost_usd(usage, book.model_rate(model), batch=batch)
    paise = (usd * book.usd_inr * 100).to_integral_value(rounding="ROUND_CEILING")
    return Paise(int(paise))
