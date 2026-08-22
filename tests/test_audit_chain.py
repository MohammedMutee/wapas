"""The audit chain must be tamper-evident. These tests are the proof."""

from __future__ import annotations

import dataclasses
import datetime as _dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from wapas.audit import GENESIS_HASH, HashChain, canonical_json, verify_chain
from wapas.audit.chain import redact
from wapas.clock import VirtualClock


def _chain(clock: VirtualClock, n: int = 5) -> HashChain:
    chain = HashChain(salt="test-salt")
    for i in range(n):
        clock.advance(_dt.timedelta(minutes=7))
        chain.append(
            at=clock.now(),
            actor="policy" if i % 2 else "system",
            event_type="gate_decision",
            payload={"step": i, "verdict": "allow", "amount_paise": 249900},
        )
    return chain


def test_chain_verifies(clock):
    chain = _chain(clock)
    result = verify_chain(chain)
    assert result.ok and result.checked == 5
    assert chain.entries[0].prev_hash == GENESIS_HASH


def test_mutated_payload_is_detected(clock):
    chain = _chain(clock)
    tampered = list(chain)
    tampered[2] = dataclasses.replace(
        tampered[2], payload={**tampered[2].payload, "amount_paise": 1}
    )
    result = verify_chain(tampered)
    assert not result.ok
    assert result.first_break_seq == 3
    assert "altered" in result.reason


def test_backdating_is_detected(clock):
    """Timestamps are inside the commitment, so an entry cannot be back-dated."""
    chain = _chain(clock)
    tampered = list(chain)
    tampered[1] = dataclasses.replace(tampered[1], at=tampered[1].at - _dt.timedelta(days=30))
    assert not verify_chain(tampered).ok


def test_deleted_entry_is_detected(clock):
    chain = _chain(clock)
    tampered = [e for e in chain if e.seq != 3]
    result = verify_chain(tampered)
    assert not result.ok and "sequence gap" in result.reason


def test_reordered_entries_are_detected(clock):
    chain = _chain(clock)
    tampered = list(chain)
    tampered[1], tampered[2] = tampered[2], tampered[1]
    assert not verify_chain(tampered).ok


def test_pii_is_digested_not_stored(clock):
    chain = HashChain(salt="s3cret")
    chain.append(
        at=clock.now(),
        actor="system",
        event_type="contact",
        payload={"phone": "+919812345678", "nested": {"email": "a@b.com"}, "amount_paise": 100},
    )
    stored = chain.entries[0].payload
    assert "+919812345678" not in canonical_json(stored)
    assert stored["phone"].startswith("sha256:")
    assert stored["nested"]["email"].startswith("sha256:")
    assert stored["amount_paise"] == 100, "non-sensitive fields stay readable"


def test_salt_prevents_reversal():
    """A holder of the chain alone cannot rainbow-table the digests."""
    a = redact({"phone": "+919812345678"}, salt="salt-a")["phone"]
    b = redact({"phone": "+919812345678"}, salt="salt-b")["phone"]
    assert a != b


def test_canonical_json_is_key_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError, match="naive datetime"):
        canonical_json({"at": _dt.datetime(2026, 1, 1, 12, 0)})


@given(st.lists(st.dictionaries(st.text(min_size=1, max_size=8), st.integers()), max_size=20))
def test_any_appended_sequence_verifies(payloads):
    """Property: a chain built only through append() always verifies."""
    clock = VirtualClock(_dt.datetime(2026, 9, 1, tzinfo=_dt.UTC))
    chain = HashChain()
    for p in payloads:
        clock.advance(_dt.timedelta(seconds=1))
        chain.append(at=clock.now(), actor="system", event_type="e", payload=p)
    assert verify_chain(chain).ok
