"""Does an episode survive a restart, and can two workers double-credit it?

These are the two questions the in-memory store cannot answer yes and no to.
They need a real database, so the whole module skips without one — announced
rather than silently passing, because a persistence suite that quietly does
nothing is worse than no persistence suite.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa

from wapas.api import PostgresEpisodeStore
from wapas.api.store import LiveEpisode, apply_event
from wapas.clock import IST
from wapas.config import settings
from wapas.db import sessionmaker_for
from wapas.domain import EpisodeState, RootCause, Surface
from wapas.money import Paise

NOW = _dt.datetime(2026, 6, 1, 12, 0, tzinfo=IST)


def _reachable(url: str) -> bool:
    try:
        engine = sa.create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(sa.text("select 1"))
        return True
    except Exception:
        return False


URL = settings().database_url
pytestmark = pytest.mark.skipif(
    not _reachable(URL),
    reason="needs Postgres: run `make up && make migrate`",
)


@pytest.fixture
def store() -> PostgresEpisodeStore:
    return PostgresEpisodeStore(sessions=sessionmaker_for(URL))


def make_episode(ref: str | None = None) -> LiveEpisode:
    return LiveEpisode(
        ref=ref or f"t_{uuid.uuid4().hex[:12]}",
        surface=Surface.PAYMENT, amount_paise=Paise(250_000), opened_at=NOW,
        state=EpisodeState.WAITING, diagnosed_cause=RootCause.AUTHENTICATION_FAILED,
        confidence=0.86, provider_id=f"plink_{uuid.uuid4().hex[:10]}",
    )


# ── survival ─────────────────────────────────────────────────────────────────


def test_an_episode_survives_a_new_store(store):
    """A new store object is what a restarted process gets."""
    episode = make_episode()
    store.put(episode)

    restarted = PostgresEpisodeStore(sessions=sessionmaker_for(URL))
    loaded = restarted.get(episode.ref)

    assert loaded is not None
    assert loaded.state is EpisodeState.WAITING
    assert loaded.amount_paise == 250_000
    assert loaded.diagnosed_cause is RootCause.AUTHENTICATION_FAILED
    assert loaded.provider_id == episode.provider_id


def test_an_episode_is_findable_by_the_providers_own_identifier(store):
    """A webhook often carries only ``plink_...`` and not our reference."""
    episode = make_episode()
    store.put(episode)
    found = PostgresEpisodeStore(sessions=sessionmaker_for(URL)).by_provider_id(
        episode.provider_id
    )
    assert found is not None and found.ref == episode.ref


def test_recovery_is_persisted(store):
    episode = make_episode()
    store.put(episode)
    apply_event(episode, event="payment_link.paid", amount_paise=250_000, at=NOW)
    store.put(episode)

    loaded = PostgresEpisodeStore(sessions=sessionmaker_for(URL)).get(episode.ref)
    assert loaded.state is EpisodeState.RECOVERED
    assert loaded.recovered_paise == 250_000
    assert loaded.closed_at is not None


# ── de-duplication that outlives the process ─────────────────────────────────


def test_a_claim_survives_a_restart(store):
    """The window an in-memory set cannot cover.

    A provider redelivers precisely when a worker went away mid-request, which
    is also when everything that worker remembered in RAM is gone.
    """
    episode = make_episode()
    store.put(episode)
    identity = "payment_link.paid:plink_x:1780000000"

    assert store.claim_event(episode, identity, event="payment_link.paid",
                             amount_paise=250_000, at=NOW) is True

    restarted = PostgresEpisodeStore(sessions=sessionmaker_for(URL))
    reloaded = restarted.get(episode.ref)
    assert restarted.claim_event(reloaded, identity, event="payment_link.paid",
                                 amount_paise=250_000, at=NOW) is False, (
        "a redelivery after a restart would have been credited twice"
    )


def test_two_workers_racing_the_same_delivery_credit_it_once(store):
    """The reason the claim is an INSERT rather than a SELECT then an INSERT.

    Eight independent stores, each with its own connection, all handed the same
    delivery at the same moment. Exactly one may win. If the check and the
    record were separate statements they would all pass the check first.
    """
    episode = make_episode()
    store.put(episode)
    identity = "payment_link.paid:plink_race:1780000000"

    def claim() -> bool:
        worker = PostgresEpisodeStore(sessions=sessionmaker_for(URL))
        return worker.claim_event(worker.get(episode.ref), identity,
                                  event="payment_link.paid",
                                  amount_paise=250_000, at=NOW)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: claim(), range(8)))

    assert sum(results) == 1, f"{sum(results)} workers each credited the payment"


def test_distinct_deliveries_are_both_claimed(store):
    """The control. A de-duplicator that rejects everything is not a
    de-duplicator, it is an outage."""
    episode = make_episode()
    store.put(episode)
    first = store.claim_event(episode, "payment.failed:p:1", event="payment.failed",
                              amount_paise=0, at=NOW)
    second = store.claim_event(episode, "payment_link.paid:p:2",
                               event="payment_link.paid", amount_paise=250_000, at=NOW)
    assert first and second


def test_the_same_delivery_on_two_episodes_is_not_confused(store):
    """The unique constraint is on (episode, identity), not on identity."""
    one, two = make_episode(), make_episode()
    store.put(one)
    store.put(two)
    identity = "payment_link.paid:shared:1780000000"
    assert store.claim_event(one, identity, event="payment_link.paid",
                             amount_paise=1, at=NOW)
    assert store.claim_event(two, identity, event="payment_link.paid",
                             amount_paise=1, at=NOW)


# ── the schema's own guarantees ──────────────────────────────────────────────


def test_the_money_invariants_survived_the_migration(store):
    """`alembic revision --autogenerate` proposed dropping all five of these.

    It read them as drift because 0001 declares them in raw SQL and the ORM
    models do not mirror them. The migration was hand-written instead; this
    asserts the result rather than trusting the review that caught it.
    """
    with store.sessions() as session:
        names = set(session.scalars(sa.text(
            "select conname from pg_constraint where contype='c' and conname like 'ck_%'"
        )).all())
    for required in (
        "ck_episode_recovered_non_negative",
        "ck_episode_cost_non_negative",
        "ck_episode_counters_non_negative",
        "ck_cost_entry_cost_non_negative",
        "ck_risk_event_amount_non_negative",
    ):
        assert required in names, f"{required} was dropped; money can now go negative"


def test_two_episodes_cannot_share_a_reference(store):
    """The merchant's reference is the identity of an episode."""
    episode = make_episode()
    store.put(episode)
    duplicate = make_episode(ref=episode.ref)
    duplicate.amount_paise = Paise(999_999)
    store.put(duplicate)

    loaded = store.get(episode.ref)
    assert loaded.amount_paise == 250_000, (
        "a second put with the same ref created a second episode instead of "
        "updating the first"
    )
