"""The virtual clock is what makes a 90-day simulation reproducible in minutes."""

from __future__ import annotations

import datetime as _dt

import pytest

from wapas.clock import IST, VirtualClock, VirtualScheduler, in_ist


def test_clock_requires_timezone():
    with pytest.raises(ValueError, match="timezone-aware"):
        VirtualClock(_dt.datetime(2026, 9, 1, 10, 0))


def test_time_never_runs_backwards(clock: VirtualClock):
    with pytest.raises(ValueError, match="does not run backwards"):
        clock.advance(_dt.timedelta(seconds=-1))
    with pytest.raises(ValueError, match="cannot rewind"):
        clock.advance_to(clock.now() - _dt.timedelta(hours=1))


def test_scheduler_pops_in_time_order(clock: VirtualClock):
    sched = VirtualScheduler(clock)
    t0 = clock.now()
    sched.schedule(t0 + _dt.timedelta(days=3), "third")
    sched.schedule(t0 + _dt.timedelta(hours=2), "first")
    sched.schedule(t0 + _dt.timedelta(days=1), "second")

    order = []
    while (item := sched.pop_due()) is not None:
        order.append(item[1])
    assert order == ["first", "second", "third"]
    assert clock.now() == t0 + _dt.timedelta(days=3)


def test_ties_break_on_insertion_order_not_object_identity(clock: VirtualClock):
    """Determinism requirement: same seed must give the same execution sequence."""
    sched = VirtualScheduler(clock)
    when = clock.now() + _dt.timedelta(hours=1)
    for label in ["a", "b", "c", "d"]:
        sched.schedule(when, label)
    assert [sched.pop_due()[1] for _ in range(4)] == ["a", "b", "c", "d"]


def test_horizon_bounds_the_simulation(clock: VirtualClock):
    sched = VirtualScheduler(clock)
    t0 = clock.now()
    sched.schedule(t0 + _dt.timedelta(days=5), "inside")
    sched.schedule(t0 + _dt.timedelta(days=200), "beyond")
    horizon = t0 + _dt.timedelta(days=90)

    assert sched.pop_due(horizon)[1] == "inside"
    assert sched.pop_due(horizon) is None, "must not run events past the horizon"
    assert len(sched) == 1


def test_quiet_hours_are_evaluated_in_ist(clock: VirtualClock):
    """A 21:30 IST message is inside quiet hours even though it is 16:00 UTC."""
    utc = _dt.datetime(2026, 9, 1, 16, 0, tzinfo=_dt.UTC)
    assert in_ist(utc).hour == 21
    assert in_ist(utc).tzinfo is IST
