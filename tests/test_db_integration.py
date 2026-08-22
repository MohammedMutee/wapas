"""Integration tests against a real Postgres.

Skipped automatically when no database is reachable, so ``make test`` stays
green on a machine with nothing running. CI brings the stack up, so these
always execute there.

These exist because the guarantees they check are *database* guarantees. An
in-memory SQLite stand-in would happily let you UPDATE the audit log, and the
test would prove nothing.
"""

from __future__ import annotations

import datetime as _dt
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError, IntegrityError

from wapas.audit import GENESIS_HASH, HashChain, verify_chain
from wapas.audit.chain import AuditEntry
from wapas.clock import IST
from wapas.config import Settings
from wapas.db import (
    AuditEntryRow,
    Base,
    Counterparty,
    Episode,
    RiskEventRow,
    erase_counterparty,
    session_scope,
    sessionmaker_for,
)
from wapas.db.session import engine_for
from wapas.domain import Arm, EpisodeState, EventKind, Surface

DB_URL = Settings(_env_file=".env").database_url


def _reachable() -> bool:
    try:
        with engine_for(DB_URL).connect() as c:
            c.execute(text("select 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(), reason="no Postgres reachable; run `make up && make migrate`"
)

NOW = _dt.datetime(2026, 9, 1, 10, 30, tzinfo=IST)


@pytest.fixture
def sm():
    return sessionmaker_for(DB_URL)


@pytest.fixture
def counterparty(sm):
    """A throwaway counterparty, cleaned up afterwards."""
    ref = f"test_{uuid.uuid4().hex[:10]}"
    with session_scope(sm) as s:
        cp = Counterparty(external_ref=ref, created_at=NOW, is_business=False)
        s.add(cp)
        s.flush()
        cid = cp.id
    yield cid
    # Best-effort cleanup. A counterparty with audit history CANNOT be deleted —
    # the audit FK is ON DELETE RESTRICT by design — so fall back to erasure,
    # which is what production does too. See wapas.db.erasure.
    try:
        with session_scope(sm) as s:
            s.query(Counterparty).filter_by(id=cid).delete()
    except IntegrityError:
        with session_scope(sm) as s:
            erase_counterparty(s, cid, at=NOW)


# ── the append-only guarantee ────────────────────────────────────────────────


def test_audit_rows_cannot_be_updated(sm):
    """Immutability enforced by Postgres, not by application discipline."""
    h = uuid.uuid4().hex + uuid.uuid4().hex
    with session_scope(sm) as s:
        s.add(AuditEntryRow(at=NOW, actor="system", event_type="t",
                            payload={"v": 1}, prev_hash=GENESIS_HASH, hash=h))

    with pytest.raises(DatabaseError, match="append-only"), session_scope(sm) as s:
        s.execute(text("update audit_entry set payload = '{\"v\": 2}' where hash = :h"),
                  {"h": h})

    with session_scope(sm) as s:
        row = s.query(AuditEntryRow).filter_by(hash=h).one()
        assert row.payload == {"v": 1}, "the original value must survive the attempt"


def test_audit_rows_cannot_be_deleted(sm):
    h = uuid.uuid4().hex + uuid.uuid4().hex
    with session_scope(sm) as s:
        s.add(AuditEntryRow(at=NOW, actor="system", event_type="t",
                            payload={}, prev_hash=GENESIS_HASH, hash=h))
    with pytest.raises(DatabaseError, match="append-only"), session_scope(sm) as s:
        s.execute(text("delete from audit_entry where hash = :h"), {"h": h})


def test_audit_table_cannot_be_truncated(sm):
    """TRUNCATE bypasses row triggers, so it has its own statement-level trigger."""
    with pytest.raises(DatabaseError, match="append-only"), session_scope(sm) as s:
        s.execute(text("truncate audit_entry"))


def test_deleting_an_episode_cannot_orphan_its_audit_trail(sm, counterparty):
    """The FK is ON DELETE RESTRICT — deleting an episode is not a back door."""
    with session_scope(sm) as s:
        ev = RiskEventRow(
            surface=Surface.PAYMENT, kind=EventKind.PAYMENT_FAILED,
            counterparty_id=counterparty, amount_paise=249900, occurred_at=NOW,
            raw={}, dedup_key=f"d_{uuid.uuid4().hex[:12]}", ingested_at=NOW,
        )
        s.add(ev)
        s.flush()
        ep = Episode(risk_event_id=ev.id, counterparty_id=counterparty,
                     state=EpisodeState.INGESTED, arm=Arm.TREATMENT,
                     surface=Surface.PAYMENT, amount_paise=249900, opened_at=NOW)
        s.add(ep)
        s.flush()
        eid = ep.id
        s.add(AuditEntryRow(at=NOW, episode_id=eid, actor="system", event_type="opened",
                            payload={}, prev_hash=GENESIS_HASH,
                            hash=uuid.uuid4().hex + uuid.uuid4().hex))

    with pytest.raises(IntegrityError), session_scope(sm) as s:
        s.query(Episode).filter_by(id=eid).delete()


# ── the chain survives a round trip through the database ─────────────────────


def test_chain_verifies_after_a_database_round_trip(sm, counterparty):
    """Hashing is only meaningful if the stored form is byte-identical."""
    # The tag goes inside the payload, not just the event_type: payloads are
    # hashed, event types are not. Without it a re-run would produce a
    # byte-identical chain and collide on the unique hash — and the previous
    # run's rows cannot be deleted to make room. That collision is the
    # append-only guarantee being inconvenient in exactly the way it should be.
    tag = uuid.uuid4().hex[:8]
    chain = HashChain(salt="integration")
    clock = NOW
    for i in range(6):
        clock += _dt.timedelta(minutes=3)
        chain.append(at=clock, actor="policy", event_type="gate_decision",
                     payload={"run": tag, "step": i, "verdict": "allow",
                              "amount_paise": 249900, "phone": "+919812345678"})

    with session_scope(sm) as s:
        for e in chain:
            s.add(AuditEntryRow(at=e.at, actor=e.actor, event_type=f"{e.event_type}_{tag}",
                                payload=e.payload, prev_hash=e.prev_hash, hash=e.hash))

    with session_scope(sm) as s:
        rows = (s.query(AuditEntryRow)
                 .filter(AuditEntryRow.event_type == f"gate_decision_{tag}")
                 .order_by(AuditEntryRow.seq).all())
        reloaded = [
            AuditEntry(seq=i + 1, at=r.at, episode_id=r.episode_id, actor=r.actor,
                       event_type="gate_decision", payload=r.payload,
                       prev_hash=r.prev_hash, hash=r.hash)
            for i, r in enumerate(rows)
        ]

    assert len(reloaded) == 6
    result = verify_chain(reloaded)
    assert result.ok, str(result)
    assert all("+919812345678" not in str(r.payload) for r in reloaded), "PII must be digested"


# ── storage-level money constraints ──────────────────────────────────────────


def test_negative_amounts_are_refused_by_the_database(sm, counterparty):
    with pytest.raises(IntegrityError, match="amount_non_negative"), session_scope(sm) as s:
        s.add(RiskEventRow(
            surface=Surface.PAYMENT, kind=EventKind.PAYMENT_FAILED,
            counterparty_id=counterparty, amount_paise=-1, occurred_at=NOW,
            raw={}, dedup_key=f"neg_{uuid.uuid4().hex[:12]}", ingested_at=NOW))


def test_dedup_key_prevents_a_replayed_webhook_opening_two_episodes(sm, counterparty):
    key = f"dup_{uuid.uuid4().hex[:12]}"

    def add():
        with session_scope(sm) as s:
            s.add(RiskEventRow(
                surface=Surface.PAYMENT, kind=EventKind.PAYMENT_FAILED,
                counterparty_id=counterparty, amount_paise=1000, occurred_at=NOW,
                raw={}, dedup_key=key, ingested_at=NOW))

    add()
    with pytest.raises(IntegrityError):
        add()


def test_orm_matches_the_migration(sm):
    """Guards against models and migrations drifting apart."""
    with engine_for(DB_URL).connect() as c:
        live = {r[0] for r in c.execute(text(
            "select tablename from pg_tables where schemaname='public'"))}
    declared = set(Base.metadata.tables)
    assert declared <= live, f"tables in the ORM but not the database: {declared - live}"


# ── right to erasure ─────────────────────────────────────────────────────────


def test_erasure_redacts_personal_data_but_keeps_the_record(sm):
    """Deleting the rows would satisfy erasure and destroy tamper-evidence.

    So erasure removes the personal data, not the record of what happened.
    """
    ref = f"test_{uuid.uuid4().hex[:10]}"
    with session_scope(sm) as s:
        cp = Counterparty(external_ref=ref, created_at=NOW, display_name="Asha Menon",
                          phone="+919812345678", email="asha@example.com",
                          channel_consent=["whatsapp", "email"])
        s.add(cp)
        s.flush()
        cid = cp.id

    with session_scope(sm) as s:
        result = erase_counterparty(s, cid, at=NOW)

    assert set(result.fields_cleared) == {"phone", "email", "display_name"}

    with session_scope(sm) as s:
        cp = s.get(Counterparty, cid)
        assert cp is not None, "the record survives; only the personal data goes"
        assert cp.phone is None and cp.email is None
        assert cp.display_name == result.pseudonym
        assert cp.channel_consent == []
        assert cp.opted_out_at is not None, "erasure implies permanent opt-out"


def test_erasure_is_idempotent(sm):
    ref = f"test_{uuid.uuid4().hex[:10]}"
    with session_scope(sm) as s:
        cp = Counterparty(external_ref=ref, created_at=NOW, phone="+919800000000")
        s.add(cp)
        s.flush()
        cid = cp.id
    with session_scope(sm) as s:
        first = erase_counterparty(s, cid, at=NOW)
    with session_scope(sm) as s:
        second = erase_counterparty(s, cid, at=NOW)
    assert first.pseudonym == second.pseudonym
    assert second.fields_cleared == (), "nothing left to clear the second time"
