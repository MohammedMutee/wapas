"""Inbound events from Razorpay, and the signature that makes them trustworthy.

A webhook endpoint is an unauthenticated URL that anyone on the internet can
POST to. If Wapas believed what arrived there, a stranger could tell it a
payment had succeeded, and the episode would close as recovered with money that
was never received. The signature is what stands between those two things.

Two rules, both enforced here rather than left to the caller:

**Verify before parsing.** The signature is computed over the raw request body.
Parsing first and verifying the parsed object is the classic mistake: it opens
the door to disagreements between what the verifier saw and what the
application acted on.

**Compare in constant time.** ``hmac.compare_digest``, not ``==``. A byte-wise
comparison that returns early leaks how much of a forged signature was correct,
which is enough to construct a valid one given patience.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

from ..domain import EventKind

RECOVERY_EVENTS = frozenset({
    "payment_link.paid",
    "payment.captured",
    "payment.failed",
    "subscription.charged",
    "subscription.halted",
})
"""Events that change an episode's state. Everything else is acknowledged and
ignored — an endpoint that only handles what it understands is easier to reason
about than one that guesses."""


class WebhookRejected(Exception):
    """The payload did not verify. It must not be parsed or acted on."""


@dataclass(frozen=True, slots=True)
class InboundEvent:
    """A verified event, normalised into the domain's vocabulary."""

    event: str
    kind: EventKind | None
    provider_id: str
    episode_ref: str
    amount_paise: int
    at: _dt.datetime
    raw: dict[str, Any]

    @property
    def is_recovery(self) -> bool:
        return self.event in {"payment_link.paid", "payment.captured", "subscription.charged"}


def verify(body: bytes, signature: str, secret: str) -> None:
    """Raise unless ``signature`` is Razorpay's HMAC-SHA256 over ``body``.

    Takes bytes rather than a parsed object on purpose: the signature covers
    the exact bytes that arrived, and re-serialising a parsed payload does not
    reliably reproduce them.
    """
    if not secret:
        raise WebhookRejected(
            "no webhook secret is configured, so no payload can be trusted. "
            "Refusing to accept the event rather than accepting it unverified."
        )
    if not signature:
        raise WebhookRejected("no signature header")

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookRejected("signature does not match the body")


def parse(body: bytes, signature: str, secret: str) -> InboundEvent:
    """Verify, then normalise. In that order, always."""
    verify(body, signature, secret)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise WebhookRejected(f"verified but unparseable: {exc}") from exc

    event = str(payload.get("event", ""))
    entity = _first_entity(payload)
    notes = entity.get("notes") or {}

    return InboundEvent(
        event=event,
        kind=_KIND_BY_EVENT.get(event),
        provider_id=str(entity.get("id", "")),
        episode_ref=str(notes.get("episode_ref", "")),
        amount_paise=int(entity.get("amount") or 0),
        at=_dt.datetime.fromtimestamp(
            int(payload.get("created_at") or 0), tz=_dt.UTC
        ),
        raw=payload,
    )


_KIND_BY_EVENT: dict[str, EventKind] = {
    "payment.failed": EventKind.PAYMENT_FAILED,
    "subscription.halted": EventKind.CHARGE_FAILED,
}


def _first_entity(payload: dict[str, Any]) -> dict[str, Any]:
    """Razorpay nests the interesting object under ``payload.<name>.entity``."""
    for wrapper in (payload.get("payload") or {}).values():
        if isinstance(wrapper, dict) and isinstance(wrapper.get("entity"), dict):
            return wrapper["entity"]
    return {}


def sign(body: bytes, secret: str) -> str:
    """Produce a valid signature. For tests and for the local demo harness."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
