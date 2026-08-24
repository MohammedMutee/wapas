"""Tests for the running service.

The webhook endpoint is the only place in this system where an untrusted party
supplies input, so most of what follows is about that: forged signatures,
replayed deliveries, events for episodes we are not working, and late events
that would reopen something already closed.

No network and no Razorpay account. The actuator is a dry run, which performs
every check it normally would and stops short of the call.
"""

from __future__ import annotations

import datetime as _dt
import json

import pytest
from fastapi.testclient import TestClient

from wapas.actuators import RazorpayActuator, sign
from wapas.api import InMemoryEpisodeStore, build_service, create_app
from wapas.clock import IST, VirtualClock
from wapas.config import Settings
from wapas.domain import EpisodeState

NOW = _dt.datetime(2026, 6, 1, 12, 0, tzinfo=IST)
SECRET = "whsec_test_only_never_real"


@pytest.fixture
def client() -> TestClient:
    config = Settings(
        razorpay_key_id="rzp_test_fake",
        razorpay_key_secret="secret",
        razorpay_webhook_secret=SECRET,
        _env_file=None,
    )
    clock = VirtualClock(NOW)
    service = build_service(
        config=config,
        store=InMemoryEpisodeStore(),
        clock=clock,
        actuator=RazorpayActuator(key_id="rzp_test_fake", dry_run=True, clock=clock),
    )
    return TestClient(create_app(service))


def open_one(client: TestClient, ref: str = "ep_1", **over) -> dict:
    body = {
        "ref": ref, "amount_paise": 250_000,
        "error_code": "GATEWAY_ERROR",
        "error_description": "Customer did not complete 3DS authentication within the time limit",
        "error_source": "customer", "error_step": "authentication",
        "rail": "card", "issuer": "HDFC",
    }
    body.update(over)
    return client.post("/episodes", json=body).json()


def webhook_body(event: str = "payment_link.paid", ref: str = "ep_1",
                 amount: int = 250_000, at: int = 1780000000) -> bytes:
    return json.dumps({
        "event": event, "created_at": at,
        "payload": {"payment_link": {"entity": {
            "id": "plink_x", "amount": amount, "notes": {"episode_ref": ref}}}},
    }).encode()


def post_webhook(client: TestClient, body: bytes, signature: str | None = None):
    return client.post(
        "/webhooks/razorpay", content=body,
        headers={"X-Razorpay-Signature": signature if signature is not None
                 else sign(body, SECRET)},
    )


# ── liveness is honest ───────────────────────────────────────────────────────


def test_health_admits_it_is_not_durable(client):
    body = client.get("/healthz").json()
    assert body["ok"] is True
    assert body["durable"] is False, (
        "the in-memory store must not imply persistence it does not have"
    )
    assert body["audit_intact"] is True


# ── the agent loop ───────────────────────────────────────────────────────────


def test_opening_an_episode_diagnoses_and_acts(client):
    result = open_one(client)
    assert result["diagnosis"]["cause"] == "authentication_failed"
    assert result["gate"]["verdict"] == "allow"
    assert result["state"] == str(EpisodeState.WAITING)
    assert result["payment_link"]["id"].startswith("dry_")


def test_the_same_episode_twice_is_not_worked_twice(client):
    open_one(client)
    again = open_one(client)
    assert again["duplicate"] is True


def test_a_gate_denial_closes_the_episode_without_actuating(client):
    """A risk decline must not produce a payment link.

    The endpoint reuses the same gate the batch evaluation does; if it could
    reach an actuator the gate had not approved, every number in the report
    would describe a different system from the one running.
    """
    result = open_one(
        client, ref="ep_risk",
        error_description="Issuer response 59: SUSPECTED FRAUD",
    )
    assert result["diagnosis"]["cause"] == "risk_declined"
    assert result["gate"]["verdict"] == "deny"
    assert "payment_link" not in result
    assert client.get("/episodes/ep_risk").json()["terminal"] is True


# ── the untrusted edge ───────────────────────────────────────────────────────


def test_a_forged_signature_is_rejected(client):
    open_one(client)
    response = post_webhook(client, webhook_body(), signature="0" * 64)
    assert response.status_code == 401
    assert response.json() == {"accepted": False}, (
        "a rejection must not tell a forger which check failed"
    )
    assert client.get("/episodes/ep_1").json()["state"] == str(EpisodeState.WAITING)


def test_an_unsigned_request_is_rejected(client):
    open_one(client)
    assert post_webhook(client, webhook_body(), signature="").status_code == 401


def test_a_tampered_amount_is_rejected(client):
    open_one(client)
    body = webhook_body()
    good = sign(body, SECRET)
    tampered = body.replace(b"250000", b"999999")
    assert post_webhook(client, tampered, signature=good).status_code == 401


def test_a_genuine_payment_closes_the_episode(client):
    open_one(client)
    result = post_webhook(client, webhook_body()).json()
    assert result["accepted"] and result["matched"] and result["changed"]
    assert result["state"] == str(EpisodeState.RECOVERED)
    episode = client.get("/episodes/ep_1").json()
    assert episode["state"] == str(EpisodeState.RECOVERED)
    assert episode["recovered"] == "₹2,500.00"


def test_a_redelivered_event_does_not_count_twice(client):
    """Providers retry until they get a 2xx. Recovery is counted once."""
    open_one(client)
    body = webhook_body()
    first = post_webhook(client, body).json()
    second = post_webhook(client, body).json()
    third = post_webhook(client, body).json()

    assert first["changed"] and not second["changed"] and not third["changed"]
    assert second["duplicate"] and third["duplicate"]
    assert client.get("/episodes/ep_1").json()["recovered"] == "₹2,500.00", (
        "a retried delivery inflated the recovered amount"
    )


def test_a_late_failure_does_not_reopen_a_recovered_episode(client):
    """Stale news about an earlier attempt is not a reversal."""
    open_one(client)
    post_webhook(client, webhook_body())
    late = post_webhook(client, webhook_body("payment.failed", at=1780000100)).json()
    assert not late["changed"]
    assert client.get("/episodes/ep_1").json()["state"] == str(EpisodeState.RECOVERED)


def test_a_failed_attempt_leaves_the_episode_open(client):
    open_one(client)
    result = post_webhook(client, webhook_body("payment.failed")).json()
    assert result["changed"]
    assert result["state"] == str(EpisodeState.WAITING)
    assert client.get("/episodes/ep_1").json()["terminal"] is False


def test_a_partial_payment_is_not_a_full_recovery(client):
    open_one(client)
    result = post_webhook(client, webhook_body(amount=100_000)).json()
    assert result["state"] == str(EpisodeState.PARTIALLY_RECOVERED)


def test_an_event_for_an_unknown_episode_is_acknowledged_not_retried(client):
    """A 4xx here makes the provider retry an event that can never match."""
    response = post_webhook(client, webhook_body(ref="never_seen"))
    assert response.status_code == 200
    assert response.json() == {"accepted": True, "matched": False,
                               "event": "payment_link.paid"}


def test_an_event_this_service_does_not_act_on_is_acknowledged(client):
    open_one(client)
    body = json.dumps({
        "event": "payment_link.partially_paid", "created_at": 1780000000,
        "payload": {"payment_link": {"entity": {
            "id": "plink_x", "amount": 1, "notes": {"episode_ref": "ep_1"}}}},
    }).encode()
    result = post_webhook(client, body).json()
    assert result["accepted"] and not result["changed"]
    assert client.get("/episodes/ep_1").json()["state"] == str(EpisodeState.WAITING)


# ── evidence ─────────────────────────────────────────────────────────────────


def test_every_step_lands_in_the_audit_chain(client):
    open_one(client)
    post_webhook(client, webhook_body())
    audit = client.get("/audit").json()
    assert audit["intact"] is True
    events = [e["event"] for e in audit["entries"]]
    assert events == ["episode_opened", "diagnosis", "gate_decision",
                      "actuation", "outcome"]


def test_a_rejected_webhook_is_recorded_without_its_contents(client):
    open_one(client)
    post_webhook(client, webhook_body(), signature="0" * 64)
    entries = client.get("/audit").json()["entries"]
    assert any(e["event"] == "webhook_rejected" for e in entries), (
        "a forgery attempt must leave a trace"
    )


def test_a_missing_episode_is_a_404(client):
    response = client.get("/episodes/nope")
    assert response.status_code == 404
    assert response.json()["found"] is False
