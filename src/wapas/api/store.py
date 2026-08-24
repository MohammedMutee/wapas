"""Episodes the service is currently working, and what happens to them.

The evaluation runs an episode start to finish inside one function call, which
is what makes it deterministic and measurable. A live system cannot do that: it
opens an episode, creates a payment link, and then *waits* — for minutes or for
days — until the customer acts or the horizon passes. The state has to live
somewhere in between.

This is that somewhere. It is deliberately small and deliberately an interface,
because the durable implementation belongs in the ``episode`` table and this
in-memory one exists so the service can be run and demonstrated without a
database.

The rule it enforces is that **a terminal episode stays terminal**. Providers
retry webhooks — Razorpay will re-deliver an event it did not get a 2xx for —
so the same "payment received" can arrive three times, and recovery must be
counted once. That is the one piece of correctness this file owns.
"""

from __future__ import annotations

import datetime as _dt
import threading
from dataclasses import dataclass, field
from typing import Protocol

from ..domain import TERMINAL_STATES, EpisodeState, RootCause, Surface
from ..money import ZERO, Paise


@dataclass
class LiveEpisode:
    """One episode being worked right now."""

    ref: str
    surface: Surface
    amount_paise: Paise
    opened_at: _dt.datetime
    state: EpisodeState = EpisodeState.INGESTED
    diagnosed_cause: RootCause | None = None
    confidence: float = 0.0
    provider_id: str = ""
    """The payment link this episode is waiting on, if any."""
    recovered_paise: Paise = ZERO
    closed_at: _dt.datetime | None = None
    terminal_reason: str = ""
    seen_events: set[str] = field(default_factory=set)
    """Provider event identities already applied. The de-duplication set."""

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_recovered(self) -> bool:
        return self.state in {EpisodeState.RECOVERED, EpisodeState.PARTIALLY_RECOVERED}


class EpisodeStore(Protocol):
    def get(self, ref: str) -> LiveEpisode | None: ...

    def put(self, episode: LiveEpisode) -> None: ...

    def by_provider_id(self, provider_id: str) -> LiveEpisode | None: ...

    def all(self) -> list[LiveEpisode]: ...


@dataclass
class InMemoryEpisodeStore:
    """Process-local episodes.

    Not durable, and the service says so at ``/healthz`` rather than implying
    otherwise. Swapping in the ``episode`` table is a change to this class and
    nothing else.
    """

    episodes: dict[str, LiveEpisode] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, ref: str) -> LiveEpisode | None:
        with self._lock:
            return self.episodes.get(ref)

    def put(self, episode: LiveEpisode) -> None:
        with self._lock:
            self.episodes[episode.ref] = episode

    def by_provider_id(self, provider_id: str) -> LiveEpisode | None:
        if not provider_id:
            return None
        with self._lock:
            for episode in self.episodes.values():
                if episode.provider_id == provider_id:
                    return episode
        return None

    def all(self) -> list[LiveEpisode]:
        with self._lock:
            return sorted(self.episodes.values(), key=lambda e: e.opened_at)


@dataclass(frozen=True, slots=True)
class Applied:
    """What an inbound event did to an episode."""

    changed: bool
    state: EpisodeState
    reason: str
    duplicate: bool = False


def apply_event(
    episode: LiveEpisode,
    *,
    event: str,
    event_identity: str,
    amount_paise: int,
    at: _dt.datetime,
) -> Applied:
    """Move an episode in response to something the provider told us.

    Three refusals, in order, and each of them is a way a live system quietly
    counts money it did not receive:

    **A repeated delivery changes nothing.** Providers retry until they get a
    2xx, so the same event arrives again whenever a response is slow or a
    deploy lands mid-request. Recovery is counted once.

    **A terminal episode does not reopen.** A late ``payment.failed`` for an
    episode already recovered is stale news about an earlier attempt, not a
    reversal, and treating it as one would erase a real payment.

    **An unrecognised event is acknowledged, not acted on.** Guessing at the
    meaning of an event this code has never seen is how an endpoint develops
    behaviour nobody designed.
    """
    if event_identity in episode.seen_events:
        return Applied(False, episode.state, "already applied", duplicate=True)
    episode.seen_events.add(event_identity)

    if episode.is_terminal:
        return Applied(False, episode.state,
                       f"episode already {episode.state}; event ignored")

    if event in {"payment_link.paid", "payment.captured", "subscription.charged"}:
        episode.recovered_paise = Paise(episode.recovered_paise + max(0, amount_paise))
        episode.state = (
            EpisodeState.RECOVERED
            if episode.recovered_paise >= episode.amount_paise
            else EpisodeState.PARTIALLY_RECOVERED
        )
        episode.closed_at = at
        episode.terminal_reason = f"payment received via {event}"
        return Applied(True, episode.state, episode.terminal_reason)

    if event == "payment.failed":
        # Not terminal. A failed attempt is the *reason* an episode exists, and
        # the recovery horizon has not passed just because one more try did not
        # land.
        episode.state = EpisodeState.WAITING
        return Applied(True, episode.state, "attempt failed; episode stays open")

    if event == "subscription.halted":
        episode.state = EpisodeState.ESCALATED
        episode.closed_at = at
        episode.terminal_reason = "subscription halted; needs a human"
        return Applied(True, episode.state, episode.terminal_reason)

    return Applied(False, episode.state, f"event {event!r} is not one this service acts on")
