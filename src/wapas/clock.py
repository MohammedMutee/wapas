"""Time.

Wapas runs in two modes and the difference is entirely contained here.

``RealClock``
    Wall-clock time. Used in live mode against the Razorpay test API.

``VirtualClock``
    A monotonically advancing simulated clock. This is what lets the evaluation
    harness compress 90 days of recovery behaviour — scheduled retries, cooldown
    windows, promise-to-pay dates, invoice aging — into a few minutes of wall
    time, deterministically.

No module outside this one may call :func:`datetime.now`. A test enforces it
(``tests/test_no_wallclock.py``), because a stray ``datetime.now()`` would make
an evaluation run irreproducible, and reproducibility is the whole pitch.
"""

from __future__ import annotations

import datetime as _dt
import heapq
import itertools
from typing import Protocol

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30), "IST")
"""Asia/Kolkata. Quiet-hours policy is evaluated in this timezone."""


class Clock(Protocol):
    """The time source. Injected everywhere; never read globally."""

    def now(self) -> _dt.datetime:
        """Current time, always timezone-aware and in UTC."""
        ...


class RealClock:
    """Wall-clock time, UTC."""

    def now(self) -> _dt.datetime:
        return _dt.datetime.now(_dt.UTC)


class VirtualClock:
    """A deterministic clock the simulation drives forward.

    Time only moves when something asks it to: either explicitly via
    :meth:`advance`, or implicitly when the scheduler pops the next due event
    via :meth:`advance_to`. Nothing happens "in the background", so a run is
    fully determined by its seed and its scenario.
    """

    def __init__(self, start: _dt.datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("VirtualClock requires a timezone-aware start time")
        self._now = start.astimezone(_dt.UTC)

    def now(self) -> _dt.datetime:
        return self._now

    def advance(self, delta: _dt.timedelta) -> _dt.datetime:
        if delta < _dt.timedelta(0):
            raise ValueError("time does not run backwards")
        self._now += delta
        return self._now

    def advance_to(self, when: _dt.datetime) -> _dt.datetime:
        when = when.astimezone(_dt.UTC)
        if when < self._now:
            raise ValueError(f"cannot rewind clock from {self._now} to {when}")
        self._now = when
        return self._now


class VirtualScheduler:
    """A deterministic priority queue of future callbacks, driven by a VirtualClock.

    Ties are broken by insertion order, never by object identity or hash, so two
    runs with the same seed execute events in exactly the same sequence.
    """

    def __init__(self, clock: VirtualClock) -> None:
        self.clock = clock
        self._heap: list[tuple[_dt.datetime, int, object]] = []
        self._counter = itertools.count()

    def schedule(self, when: _dt.datetime, payload: object) -> None:
        heapq.heappush(self._heap, (when.astimezone(_dt.UTC), next(self._counter), payload))

    def pop_due(self, horizon: _dt.datetime | None = None) -> tuple[_dt.datetime, object] | None:
        """Advance the clock to the next scheduled item and return it.

        Returns ``None`` when the queue is empty, or when the next item falls
        beyond ``horizon`` (used to bound a simulation to N days).
        """
        if not self._heap:
            return None
        when, _, payload = self._heap[0]
        if horizon is not None and when > horizon:
            return None
        heapq.heappop(self._heap)
        self.clock.advance_to(when)
        return when, payload

    def __len__(self) -> int:
        return len(self._heap)


def in_ist(moment: _dt.datetime) -> _dt.datetime:
    """Convert an instant to IST for policy checks that are stated in local time."""
    return moment.astimezone(IST)
