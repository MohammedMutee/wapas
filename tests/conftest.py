"""Shared fixtures.

Every test runs on a deterministic clock. There is no wall-clock time anywhere
in the suite, which is why the whole suite is reproducible.
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))  # sim/ and eval/ are top-level, not packaged

from wapas.clock import IST, VirtualClock  # noqa: E402  (needs sys.path above)

RUN_START = _dt.datetime(2026, 9, 1, 10, 30, tzinfo=IST)


@pytest.fixture
def clock() -> VirtualClock:
    return VirtualClock(RUN_START)
