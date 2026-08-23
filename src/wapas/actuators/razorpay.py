"""Razorpay test-mode actuator.

Built against the API rather than against the documentation: every shape below
was confirmed by calling the sandbox and reading what came back. Two findings
changed the design.

**``reference_id`` is genuinely unique.** A second create with the same value
is rejected with ``BadRequestError: payment link with given reference_id …
already exists``. That is a real idempotency constraint and worth relying on.

**But you cannot look a link up by it.** ``payment_link.all({"reference_id":
…})`` returns nothing — the filter is not honoured. So the obvious recovery,
"on duplicate, fetch the original", does not work, and idempotency needs a
local ledger plus a reconciliation scan for the case where the ledger and the
provider disagree.

That case is not hypothetical. It is what happens when the process dies between
the API call succeeding and the write recording it, which is the single most
likely way a recovery agent charges someone twice.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from ..clock import Clock, RealClock
from ..domain import GateDecision, Tool
from ..money import Paise
from .base import (
    ActuationRefused,
    ActuationResult,
    IdempotencyStore,
    InMemoryIdempotencyStore,
    idempotency_key,
    require_approval,
)

LIVE_PREFIX = "rzp_live_"


class RazorpayActuator:
    """Creates and inspects payment links against Razorpay **test mode**.

    ``dry_run`` performs every check — gate approval, idempotency, payload
    construction — and stops short of the network. The simulator and the test
    suite run this way, so the code path they exercise is the real one minus
    exactly one line.
    """

    def __init__(
        self,
        *,
        key_id: str,
        key_secret: str | None = None,
        client: Any = None,
        store: IdempotencyStore | None = None,
        clock: Clock | None = None,
        dry_run: bool = False,
        audit=None,
    ) -> None:
        if key_id.startswith(LIVE_PREFIX):
            raise ValueError(
                "a live Razorpay key reached the actuator. Wapas is test-mode only "
                "and has no live code path; refusing to construct."
            )
        if not key_id.startswith("rzp_test_") and not dry_run:
            raise ValueError(f"unrecognised Razorpay key prefix: {key_id[:9]}...")

        self.key_id = key_id
        self.dry_run = dry_run
        self.store = store if store is not None else InMemoryIdempotencyStore()
        self.clock = clock or RealClock()
        self.audit = audit
        self._client = client
        self._secret = key_secret
        self.calls_made = 0
        """Network calls actually performed. Replays and dry runs do not count."""

    # ── the client ───────────────────────────────────────────────────────────

    @property
    def client(self) -> Any:
        if self._client is None:
            if self.dry_run:
                raise RuntimeError("dry_run actuator must not need a client")
            import razorpay  # imported lazily: the simulator never needs it

            self._client = razorpay.Client(auth=(self.key_id, self._secret or ""))
        return self._client

    # ── the one action that moves money towards the merchant ─────────────────

    def create_payment_link(
        self,
        decision: GateDecision,
        *,
        episode_ref: str,
        step: int,
        amount: Paise,
        description: str = "",
        expire_after_hours: int = 72,
    ) -> ActuationResult:
        """Offer the counterparty somewhere to pay.

        The recovery action that works when re-presenting the original
        instrument cannot: a dead card, an abandoned 3DS flow, a mandate that
        needs re-signing.
        """
        require_approval(decision)
        if decision.action is not None and decision.action.tool is not Tool.CREATE_PAYMENT_LINK:
            raise ActuationRefused(
                f"this actuator creates payment links; the gate approved "
                f"{decision.action.tool}"
            )
        if int(amount) <= 0:
            raise ActuationRefused(f"refusing to create a payment link for {amount} paise")

        key = idempotency_key("wapas", episode_ref, step)
        seen = self.store.get(key)
        if seen is not None:
            return ActuationResult(**{**_as_dict(seen), "replayed": True})

        now = self.clock.now()
        payload = {
            "amount": int(amount),
            "currency": "INR",
            "accept_partial": False,
            "description": (description or "Payment recovery")[:2048],
            "reference_id": key,
            "expire_by": int((now + _dt.timedelta(hours=expire_after_hours)).timestamp()),
            "reminder_enable": False,
            # Wapas owns contact timing and frequency; the gate decides when a
            # counterparty may be messaged and on which channel. Letting the
            # provider send its own reminders would put contacts outside the
            # policy that exists to bound them.
            "notify": {"sms": False, "email": False},
            "notes": {"episode_ref": episode_ref, "step": str(step), "source": "wapas"},
        }

        if self.dry_run:
            result = ActuationResult(
                tool=Tool.CREATE_PAYMENT_LINK, ok=True, idempotency_key=key,
                provider_id=f"dry_{key}", detail={"payload": payload, "dry_run": True},
                at=now,
            )
            self.store.put(key, result)
            self._record(result, episode_ref)
            return result

        try:
            self.calls_made += 1
            link = self.client.payment_link.create(payload)
            result = ActuationResult(
                tool=Tool.CREATE_PAYMENT_LINK, ok=True, idempotency_key=key,
                provider_id=str(link.get("id", "")),
                detail={"short_url": link.get("short_url", ""),
                        "status": link.get("status", ""),
                        "amount": link.get("amount")},
                at=now,
            )
        except Exception as exc:  # provider SDK raises its own error types
            if _is_duplicate(exc):
                result = self._reconcile(key, now)
            else:
                result = ActuationResult(
                    tool=Tool.CREATE_PAYMENT_LINK, ok=False, idempotency_key=key,
                    error=f"{type(exc).__name__}: {exc}"[:400], at=now,
                )

        self.store.put(key, result)
        self._record(result, episode_ref)
        return result

    def _reconcile(self, key: str, now: _dt.datetime) -> ActuationResult:
        """The provider already has this link and we did not know.

        Reached when the process died between the call succeeding and the write
        recording it. Since the ``reference_id`` filter is not honoured, the
        only way back to the original is to scan and match client-side. Slow,
        rare, and much better than creating a second link.
        """
        try:
            self.calls_made += 1
            page = self.client.payment_link.all({"count": 100})
            for item in page.get("items", []):
                if item.get("reference_id") == key:
                    return ActuationResult(
                        tool=Tool.CREATE_PAYMENT_LINK, ok=True, idempotency_key=key,
                        provider_id=str(item.get("id", "")),
                        detail={"short_url": item.get("short_url", ""),
                                "status": item.get("status", "")},
                        reconciled=True, at=now,
                    )
        except Exception as exc:
            return ActuationResult(
                tool=Tool.CREATE_PAYMENT_LINK, ok=False, idempotency_key=key,
                error=f"duplicate, and reconciliation failed: {type(exc).__name__}: {exc}"[:400],
                at=now,
            )
        return ActuationResult(
            tool=Tool.CREATE_PAYMENT_LINK, ok=False, idempotency_key=key,
            error="provider reports this reference_id exists but it was not found in "
                  "the first 100 links; not creating a second one",
            at=now,
        )

    # ── read-only ────────────────────────────────────────────────────────────

    def fetch_payment_link(self, provider_id: str) -> dict[str, Any]:
        """Current state of a link. Read-only, so no gate decision is needed."""
        if self.dry_run:
            return {"id": provider_id, "status": "created", "amount_paid": 0}
        self.calls_made += 1
        return dict(self.client.payment_link.fetch(provider_id))

    def cancel_payment_link(self, provider_id: str) -> dict[str, Any]:
        """Withdraw a link. Used when an episode closes before it is paid."""
        if self.dry_run:
            return {"id": provider_id, "status": "cancelled"}
        self.calls_made += 1
        return dict(self.client.payment_link.cancel(provider_id))

    # ── evidence ─────────────────────────────────────────────────────────────

    def _record(self, result: ActuationResult, episode_ref: str) -> None:
        if self.audit is None:
            return
        self.audit.append(
            at=result.at or self.clock.now(), actor="actuator",
            event_type="actuation",
            payload={
                "ref": episode_ref, "tool": str(result.tool), "ok": result.ok,
                "provider_id": result.provider_id,
                "idempotency_key": result.idempotency_key,
                "replayed": result.replayed, "reconciled": result.reconciled,
                "error": result.error,
            },
        )


def _as_dict(result: ActuationResult) -> dict[str, Any]:
    import dataclasses

    return {f.name: getattr(result, f.name) for f in dataclasses.fields(result)}


def _is_duplicate(exc: Exception) -> bool:
    text = str(exc).lower()
    return "reference_id" in text and "already exists" in text
