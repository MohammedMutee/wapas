"""The actuator boundary: the only place Wapas causes an effect in the world.

Everything upstream of here is reasoning — diagnosis, planning, policy. This is
where a message is sent, a payment is re-presented, a link is created. Three
properties are enforced at the boundary rather than trusted to callers.

**Nothing actuates without a gate ruling.** An actuator takes a
:class:`~wapas.domain.GateDecision`, not a
:class:`~wapas.domain.ProposedAction`. There is no signature that accepts a
bare proposal, so "the policy gate cannot be bypassed" is a property of the
types rather than a convention someone might forget. A decision carrying
``DENY`` raises.

**Every call is idempotent.** ``policies/money.yaml`` requires it of all
actuators. Retrying after a timeout is normal — the network fails, a worker is
restarted mid-episode — and a recovery system that creates two payment links
because it did not hear back the first time is worse than one that does
nothing.

**Nothing reaches a live account.** The configuration layer refuses live keys
outright; this layer refuses again, because a guarantee this size is worth
stating twice.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..domain import GateDecision, GateVerdict, Tool


class ActuationRefused(RuntimeError):
    """The action was never attempted. Distinct from a failed attempt."""


@dataclass(frozen=True, slots=True)
class ActuationResult:
    """What happened, in enough detail to audit and to replay."""

    tool: Tool
    ok: bool
    idempotency_key: str
    provider_id: str = ""
    """The provider's own identifier, e.g. ``plink_TTJusNBqu4dNIi``."""
    detail: dict[str, Any] = field(default_factory=dict)
    replayed: bool = False
    """True when a previous identical call was reused instead of a new one."""
    reconciled: bool = False
    """True when the provider already had this action and we adopted it.

    Distinct from ``replayed``: replay means *we* remembered, reconciliation
    means we forgot and the provider remembered for us. The second is the
    interesting one, because it is what happens after a crash between the call
    and the write.
    """
    error: str = ""
    at: _dt.datetime | None = None

    @property
    def caused_an_effect(self) -> bool:
        """Whether this call changed anything outside the process."""
        return self.ok and not (self.replayed or self.reconciled)


class IdempotencyStore(Protocol):
    """Remembers what has already been actuated, keyed by intent."""

    def get(self, key: str) -> ActuationResult | None: ...

    def put(self, key: str, result: ActuationResult) -> None: ...


@dataclass
class InMemoryIdempotencyStore:
    """Process-local store. Correct for a single run; not durable.

    Real deployments write to the ``action`` table, which survives a restart.
    This exists so the test suite and the simulator never need a database, and
    it is deliberately the *weaker* implementation — the reconciliation path
    below is what covers its weakness, and having a weak store here means that
    path is exercised rather than theoretical.
    """

    entries: dict[str, ActuationResult] = field(default_factory=dict)

    def get(self, key: str) -> ActuationResult | None:
        return self.entries.get(key)

    def put(self, key: str, result: ActuationResult) -> None:
        self.entries[key] = result


def require_approval(decision: GateDecision) -> None:
    """Refuse anything the gate did not approve.

    Called first in every actuator. ``MODIFY`` is an approval: the gate
    rewrote the action — rescheduled it, capped a concession — and the rewritten
    action is the one to perform.
    """
    if decision.verdict is GateVerdict.DENY:
        raise ActuationRefused(
            f"the gate denied this action: {', '.join(decision.reasons) or 'no reason given'}"
        )
    if decision.action is None:
        raise ActuationRefused("an approved decision carried no action to perform")


def idempotency_key(*parts: object) -> str:
    """A stable key for one intent.

    Built from the episode and the step rather than from a timestamp or a
    random value, so the *same* intent retried after a crash produces the same
    key and the *next* intent does not.
    """
    return "-".join(str(p).replace(" ", "_") for p in parts if str(p))
