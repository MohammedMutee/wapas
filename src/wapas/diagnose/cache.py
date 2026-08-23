"""A content-addressed cache of model diagnoses.

Keyed on the prompt digest, which already covers the system prompt, the user
prompt, the model and the structured mode — so any change to the prompt or the
model invalidates every entry automatically rather than silently serving stale
answers from a different question.

It exists for three reasons, in order of importance:

**Reproducibility.** ``make eval`` must produce the same report twice. A model
call is not deterministic even at temperature 0, so a cached run is the only
way a seed fully determines the output.

**Cost.** Amounts are banded and the failure signals come from a fixed pool, so
a 2,000-episode arm collapses to a few hundred distinct prompts. Without the
cache the same question is asked and paid for hundreds of times.

**Not hammering a free endpoint.** The NVIDIA developer tier is a courtesy.

The cache is a plain JSON file so it can be inspected, diffed and deleted by
hand. It is *not* committed: a cache in version control is a way of shipping
results without shipping the thing that produced them.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(".cache/diagnoses.json")


@dataclass
class DiagnosisCache:
    """Prompt digest to stored payload. Written atomically."""

    path: Path = DEFAULT_PATH
    entries: dict[str, dict[str, Any]] = None  # type: ignore[assignment]
    hits: int = 0
    misses: int = 0
    dirty: bool = False

    def __post_init__(self) -> None:
        if self.entries is None:
            self.entries = {}
        if self.path.exists():
            try:
                self.entries = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                # A corrupt cache is a nuisance, never a failure: recompute.
                self.entries = {}

    def get(self, digest: str) -> dict[str, Any] | None:
        found = self.entries.get(digest)
        if found is None:
            self.misses += 1
        else:
            self.hits += 1
        return found

    def put(self, digest: str, payload: dict[str, Any]) -> None:
        self.entries[digest] = payload
        self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=self.path.parent, delete=False, encoding="utf-8"
        ) as handle:
            json.dump(self.entries, handle, indent=1, sort_keys=True)
            temp = Path(handle.name)
        temp.replace(self.path)
        self.dirty = False

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
