"""Tests for the boundary where Wapas causes an effect.

No network. The Razorpay client is faked, so these run in CI and on a plane,
and a separate live script (`scripts/live_demo.py`) exercises the real sandbox.
"""

from __future__ import annotations

import datetime as _dt
import json

import pytest

from wapas.actuators import (
    ActuationRefused,
    InMemoryIdempotencyStore,
    RazorpayActuator,
    WebhookRejected,
    parse,
    sign,
    verify,
)
from wapas.clock import IST, VirtualClock
from wapas.domain import GateDecision, GateVerdict, ProposedAction, Tool
from wapas.money import Paise

NOW = _dt.datetime(2026, 6, 1, 12, 0, tzinfo=IST)
SECRET = "whsec_test_do_not_use_anywhere_real"


def approved(tool: Tool = Tool.CREATE_PAYMENT_LINK) -> GateDecision:
    return GateDecision(
        verdict=GateVerdict.ALLOW,
        action=ProposedAction(tool=tool, rationale="test"),
        reasons=(), policy_version="test/1",
    )


def denied() -> GateDecision:
    return GateDecision(
        verdict=GateVerdict.DENY, action=None,
        reasons=("opted_out",), policy_version="test/1",
    )


class FakeLinks:
    def __init__(self, fail_with: Exception | None = None) -> None:
        self.created: list[dict] = []
        self.fail_with = fail_with
        self.listed = 0

    def create(self, payload):
        if self.fail_with is not None:
            raise self.fail_with
        self.created.append(payload)
        return {"id": f"plink_{len(self.created)}", "status": "created",
                "short_url": "https://rzp.io/x", "amount": payload["amount"],
                "reference_id": payload["reference_id"]}

    def all(self, _params=None):
        self.listed += 1
        return {"items": [{"id": "plink_existing", "status": "created",
                           "short_url": "https://rzp.io/e",
                           "reference_id": "wapas-ep_1-0"}]}

    def fetch(self, pid):
        return {"id": pid, "status": "paid", "amount_paid": 250000}

    def cancel(self, pid):
        return {"id": pid, "status": "cancelled"}


class FakeClient:
    def __init__(self, **kw) -> None:
        self.payment_link = FakeLinks(**kw)


def actuator(client=None, **kw) -> RazorpayActuator:
    return RazorpayActuator(
        key_id="rzp_test_fake", key_secret="s", client=client or FakeClient(),
        clock=VirtualClock(NOW), **kw,
    )


# ── nothing actuates without a gate ruling ───────────────────────────────────


def test_a_denied_action_is_never_attempted():
    client = FakeClient()
    with pytest.raises(ActuationRefused, match="denied"):
        actuator(client).create_payment_link(
            denied(), episode_ref="ep_1", step=0, amount=Paise(250_000)
        )
    assert client.payment_link.created == [], "a denied action reached the network"


def test_an_actuator_refuses_a_tool_it_does_not_implement():
    with pytest.raises(ActuationRefused, match="approved"):
        actuator().create_payment_link(
            approved(Tool.SEND_MESSAGE), episode_ref="ep_1", step=0,
            amount=Paise(250_000),
        )


def test_a_zero_amount_link_is_refused():
    with pytest.raises(ActuationRefused):
        actuator().create_payment_link(
            approved(), episode_ref="ep_1", step=0, amount=Paise(0)
        )


# ── live keys ────────────────────────────────────────────────────────────────


def test_a_live_key_cannot_construct_an_actuator():
    with pytest.raises(ValueError, match="live"):
        RazorpayActuator(key_id="rzp_live_realmoney", key_secret="s")


# ── idempotency ──────────────────────────────────────────────────────────────


def test_the_same_intent_twice_creates_one_link():
    client = FakeClient()
    act = actuator(client)
    first = act.create_payment_link(approved(), episode_ref="ep_1", step=0,
                                    amount=Paise(250_000))
    second = act.create_payment_link(approved(), episode_ref="ep_1", step=0,
                                     amount=Paise(250_000))
    assert len(client.payment_link.created) == 1, "a retry created a second link"
    assert second.replayed and not first.replayed
    assert second.provider_id == first.provider_id
    assert not second.caused_an_effect


def test_a_different_step_is_a_different_intent():
    client = FakeClient()
    act = actuator(client)
    act.create_payment_link(approved(), episode_ref="ep_1", step=0, amount=Paise(1000))
    act.create_payment_link(approved(), episode_ref="ep_1", step=1, amount=Paise(1000))
    assert len(client.payment_link.created) == 2


def test_a_crash_between_the_call_and_the_write_does_not_double_charge():
    """The reconciliation path, and the reason it exists.

    The store is empty — as it would be after a restart — but the provider
    already has the link. Razorpay rejects the duplicate, and because its
    ``reference_id`` filter is not honoured the only way back is to scan. The
    wrong behaviour here is to treat the rejection as a failure and try again
    with a fresh key, which is exactly how a customer gets two payment links.
    """
    duplicate = Exception(
        "BadRequestError: payment link with given reference_id: wapas-ep_1-0 "
        "already exists. Please create a payment link with a different reference_id"
    )
    client = FakeClient(fail_with=duplicate)
    result = actuator(client, store=InMemoryIdempotencyStore()).create_payment_link(
        approved(), episode_ref="ep_1", step=0, amount=Paise(250_000)
    )
    assert result.ok and result.reconciled
    assert result.provider_id == "plink_existing"
    assert not result.caused_an_effect
    assert client.payment_link.listed == 1


def test_an_unrelated_failure_is_reported_not_reconciled():
    client = FakeClient(fail_with=Exception("ServerError: upstream exploded"))
    result = actuator(client).create_payment_link(
        approved(), episode_ref="ep_1", step=0, amount=Paise(250_000)
    )
    assert not result.ok and not result.reconciled
    assert "upstream exploded" in result.error
    assert client.payment_link.listed == 0, "a server error must not trigger a scan"


# ── dry run ──────────────────────────────────────────────────────────────────


def test_dry_run_does_everything_except_the_network():
    act = RazorpayActuator(key_id="rzp_test_fake", dry_run=True, clock=VirtualClock(NOW))
    result = act.create_payment_link(approved(), episode_ref="ep_1", step=0,
                                     amount=Paise(250_000))
    assert result.ok and act.calls_made == 0
    payload = result.detail["payload"]
    assert payload["amount"] == 250_000 and payload["currency"] == "INR"
    assert payload["notify"] == {"sms": False, "email": False}, (
        "provider-side reminders would put contacts outside the policy gate"
    )


def test_dry_run_still_refuses_a_denied_action():
    act = RazorpayActuator(key_id="rzp_test_fake", dry_run=True, clock=VirtualClock(NOW))
    with pytest.raises(ActuationRefused):
        act.create_payment_link(denied(), episode_ref="ep_1", step=0, amount=Paise(1000))


# ── webhooks ─────────────────────────────────────────────────────────────────


def _body(event: str = "payment_link.paid", ref: str = "ep_1") -> bytes:
    return json.dumps({
        "event": event, "created_at": 1780000000,
        "payload": {"payment_link": {"entity": {
            "id": "plink_abc", "amount": 250000, "notes": {"episode_ref": ref},
        }}},
    }).encode()


def test_a_forged_webhook_is_rejected():
    """An unauthenticated URL anyone can POST to. Without this, a stranger can
    tell Wapas a payment succeeded and the episode closes as recovered."""
    with pytest.raises(WebhookRejected, match="signature"):
        parse(_body(), "0" * 64, SECRET)


def test_a_tampered_body_is_rejected():
    body = _body()
    good = sign(body, SECRET)
    with pytest.raises(WebhookRejected):
        parse(body.replace(b"250000", b"999999"), good, SECRET)


def test_a_missing_secret_refuses_everything():
    body = _body()
    with pytest.raises(WebhookRejected, match="no webhook secret"):
        verify(body, sign(body, SECRET), "")


def test_a_missing_signature_is_rejected():
    with pytest.raises(WebhookRejected, match="no signature"):
        verify(_body(), "", SECRET)


def test_a_genuine_webhook_parses_into_the_domain():
    body = _body()
    event = parse(body, sign(body, SECRET), SECRET)
    assert event.event == "payment_link.paid"
    assert event.provider_id == "plink_abc"
    assert event.episode_ref == "ep_1"
    assert event.amount_paise == 250_000
    assert event.is_recovery


def test_a_failure_event_is_not_a_recovery():
    body = json.dumps({
        "event": "payment.failed", "created_at": 1780000000,
        "payload": {"payment": {"entity": {"id": "pay_1", "amount": 100,
                                           "notes": {"episode_ref": "ep_2"}}}},
    }).encode()
    event = parse(body, sign(body, SECRET), SECRET)
    assert not event.is_recovery
    assert event.kind is not None


def test_verified_but_unparseable_is_still_rejected():
    body = b"{not json"
    with pytest.raises(WebhookRejected, match="unparseable"):
        parse(body, sign(body, SECRET), SECRET)
