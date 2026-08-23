"""Tests for the cross-episode outage detector.

Two properties decide whether this is evidence or a liability.

**Causality.** A view queried at time ``t`` must count nothing that happened
after ``t``. A detector that peeks at the next hour would report an accuracy no
deployment can reach, and it would do it silently.

**Precision over recall.** Missing an outage costs a diagnosis. Inventing one
sends a retry into a wall and tells the planner to wait for a recovery that was
never coming.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from sim import build_population, load_params
from wapas.clock import IST
from wapas.diagnose.fleet import FleetView

START = _dt.datetime(2026, 6, 1, tzinfo=IST)


@dataclass
class _Ep:
    issuer: str
    occurred_at: _dt.datetime


def _stream(issuer: str, minutes: list[int]) -> list[_Ep]:
    return [_Ep(issuer, START + _dt.timedelta(minutes=m)) for m in minutes]


# ── causality ────────────────────────────────────────────────────────────────


def test_a_view_never_counts_the_future():
    """The property that would invalidate every number if it broke."""
    later = _stream("HDFC", [0, 1, 2, 3, 4, 5] + [600 + i for i in range(50)])
    view = FleetView.from_episodes(later)
    at_start = view.signal_at("HDFC", START + _dt.timedelta(minutes=5))
    assert at_start is not None
    assert at_start.failures_in_window == 6, (
        "the spike at minute 600 must be invisible at minute 5"
    )
    assert not at_start.spiking


def test_the_same_spike_is_visible_once_it_has_happened():
    stream = _stream("HDFC", [0, 1, 2, 3, 4, 5] + [600 + i for i in range(50)])
    view = FleetView.from_episodes(stream)
    during = view.signal_at("HDFC", START + _dt.timedelta(minutes=640))
    assert during is not None and during.spiking


# ── it must not invent outages ───────────────────────────────────────────────


def test_steady_traffic_is_never_a_spike():
    steady = _stream("SBI", list(range(0, 6000, 10)))
    view = FleetView.from_episodes(steady)
    for minute in (100, 1000, 3000, 5000):
        signal = view.signal_at("SBI", START + _dt.timedelta(minutes=minute))
        assert signal is not None and not signal.spiking


def test_a_thin_window_is_not_an_outage():
    """Two failures in an hour on a quiet bank is a Tuesday."""
    view = FleetView.from_episodes(_stream("Yes", [0, 30]))
    signal = view.signal_at("Yes", START + _dt.timedelta(minutes=45))
    assert signal is not None and not signal.spiking


def test_an_unknown_issuer_answers_nothing_rather_than_zero():
    view = FleetView.from_episodes(_stream("HDFC", [0, 1, 2]))
    assert view.signal_at("ICICI", START) is None
    assert view.signal_at("", START) is None


def test_one_bank_going_down_does_not_implicate_another():
    stream = _stream("HDFC", [600 + i for i in range(60)]) + _stream("SBI", list(range(0, 6000, 10)))
    view = FleetView.from_episodes(stream)
    at = START + _dt.timedelta(minutes=640)
    assert view.signal_at("HDFC", at).spiking
    assert not view.signal_at("SBI", at).spiking


# ── against the simulator's ground truth ─────────────────────────────────────


def test_precision_holds_on_the_evaluation_world():
    """Tuned on history, asserted here. Precision is the binding constraint."""
    population = build_population(load_params(), run_seed=20260901, start=START)
    episodes = [e for e in population.episodes if e.issuer]
    view = FleetView.from_episodes(episodes)

    hits = false_alarms = misses = 0
    for ep in episodes:
        signal = view.signal_at(ep.issuer, ep.occurred_at)
        spiking = bool(signal and signal.spiking)
        down = ep.issuer_down_until is not None
        hits += spiking and down
        false_alarms += spiking and not down
        misses += (not spiking) and down

    precision = hits / max(1, hits + false_alarms)
    recall = hits / max(1, hits + misses)
    assert precision >= 0.95, f"precision fell to {precision:.1%}; it sends retries into walls"
    assert recall >= 0.55, f"recall fell to {recall:.1%}; the detector has stopped detecting"


def test_the_detector_uses_a_parameter_that_used_to_be_inert():
    """`bursts_per_90_days` moved nothing until something read across episodes.

    Regression for D29: the simulator argued at length for correlated outages
    while nothing in the system consumed the correlation.
    """
    params = load_params()
    quiet = build_population(params.perturbed(0.4, ("bursts_per_90_days",)),
                             run_seed=20260901, start=START)
    busy = build_population(params.perturbed(2.0, ("bursts_per_90_days",)),
                            run_seed=20260901, start=START)

    def spikes(population) -> int:
        episodes = [e for e in population.episodes if e.issuer]
        view = FleetView.from_episodes(episodes)
        return sum(
            1 for e in episodes
            if (s := view.signal_at(e.issuer, e.occurred_at)) and s.spiking
        )

    many, few = spikes(busy), spikes(quiet)
    assert abs(many - few) > 0.25 * max(many, few), (
        f"outage frequency moved detections only from {few} to {many}; the "
        f"bursty outage model is still decoration"
    )
    # The direction is worth knowing, and it is the opposite of the obvious
    # guess. The *number* of outage-caused failures is fixed by the cause
    # distribution, so more bursts spreads the same failures across more
    # windows and each one is thinner and harder to see. Fewer, larger outages
    # are easier to detect than many small ones — which is also true in
    # production, and is the case for alerting on concentration rather than
    # on volume.
    assert few > many
