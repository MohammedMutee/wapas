"""The durable episode store.

The in-memory store is correct for one process that never restarts. This one is
correct for the situation a deployment is actually in: several workers, rolling
deploys, and a provider that redelivers events precisely when a worker went
away mid-request.

One design point carries most of the value. **De-duplication is an INSERT, not
a SELECT followed by an INSERT.** Asking "have I seen this delivery?" and then
recording it leaves a window between the two questions, and two workers holding
the same retry will both pass the check and both credit the payment. Inserting
into a table with a unique constraint and catching the violation collapses the
question and the answer into one atomic act, which is the only version that is
true under concurrency.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..db.models import Counterparty, Episode, RiskEventRow, WebhookDelivery
from ..domain import EpisodeState, RootCause, Surface
from ..money import Paise
from .store import LiveEpisode

LIVE_ARM = "treatment"
"""Live traffic is not an experiment arm, but the column is not nullable and
mislabelling it would corrupt any later analysis that groups by arm. Live
episodes are marked as treated because that is what they are: a real customer
who received a real intervention."""


@dataclass
class PostgresEpisodeStore:
    """Episodes in the ``episode`` table, deliveries in ``webhook_delivery``."""

    sessions: sessionmaker[Session]

    # ── reads ────────────────────────────────────────────────────────────────

    def get(self, ref: str) -> LiveEpisode | None:
        with self.sessions() as session:
            row = session.scalar(select(Episode).where(Episode.ref == ref))
            return self._to_live(session, row) if row else None

    def by_provider_id(self, provider_id: str) -> LiveEpisode | None:
        if not provider_id:
            return None
        with self.sessions() as session:
            row = session.scalar(
                select(Episode).where(Episode.provider_id == provider_id)
            )
            return self._to_live(session, row) if row else None

    def all(self) -> list[LiveEpisode]:
        with self.sessions() as session:
            rows = session.scalars(
                select(Episode).where(Episode.ref.is_not(None)).order_by(Episode.opened_at)
            ).all()
            return [self._to_live(session, r) for r in rows]

    # ── writes ───────────────────────────────────────────────────────────────

    def put(self, episode: LiveEpisode) -> None:
        with self.sessions() as session, session.begin():
            row = session.scalar(select(Episode).where(Episode.ref == episode.ref))
            if row is None:
                row = self._create(session, episode)
            row.state = str(episode.state)
            row.root_cause = (str(episode.diagnosed_cause)
                              if episode.diagnosed_cause else None)
            row.diagnosis_confidence = episode.confidence
            row.provider_id = episode.provider_id or None
            row.recovered_paise = int(episode.recovered_paise)
            row.closed_at = episode.closed_at
            row.terminal_reason = (episode.terminal_reason or None)[:120] \
                if episode.terminal_reason else None

    def claim_event(self, episode: LiveEpisode, identity: str, *,
                    event: str, amount_paise: int, at: _dt.datetime) -> bool:
        """Insert the delivery, or discover the database already has it.

        The ``IntegrityError`` is the answer, not an error. It is raised by the
        unique index the moment a second worker tries to record the same
        delivery, and it is raised whether that worker is in this process, on
        another machine, or the same machine after a restart wiped whatever was
        being remembered in RAM.
        """
        with self.sessions() as session:
            row = session.scalar(select(Episode).where(Episode.ref == episode.ref))
            if row is None:
                return False
            session.add(WebhookDelivery(
                id=uuid.uuid4(), episode_id=row.id, event_identity=identity[:200],
                event=event[:60], amount_paise=max(0, amount_paise), received_at=at,
            ))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return False
            return True

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _create(self, session: Session, episode: LiveEpisode) -> Episode:
        """A live episode needs the rows the schema says an episode has.

        The counterparty carries no identifying detail: the API deliberately
        never receives a phone number or an email, so there is nothing to
        store and nothing to leak. It exists because an episode belongs to
        someone, and the schema is right to insist on that.
        """
        counterparty = Counterparty(
            id=uuid.uuid4(), external_ref=f"live:{episode.ref}",
            is_business=False, created_at=episode.opened_at,
        )
        risk_event = RiskEventRow(
            id=uuid.uuid4(), counterparty_id=counterparty.id,
            surface=str(episode.surface), kind="payment_failed",
            amount_paise=int(episode.amount_paise),
            dedup_key=f"live:{episode.ref}", occurred_at=episode.opened_at,
            ingested_at=episode.opened_at,
            raw={"source": "live", "ref": episode.ref},
        )
        row = Episode(
            id=uuid.uuid4(), ref=episode.ref,
            risk_event_id=risk_event.id, counterparty_id=counterparty.id,
            state=str(episode.state), arm=LIVE_ARM, surface=str(episode.surface),
            amount_paise=int(episode.amount_paise), spend_paise=0,
            recovered_paise=0, cost_paise=0, opened_at=episode.opened_at,
        )
        session.add_all([counterparty, risk_event, row])
        session.flush()
        return row

    def _to_live(self, session: Session, row: Episode) -> LiveEpisode:
        seen = set(session.scalars(
            select(WebhookDelivery.event_identity)
            .where(WebhookDelivery.episode_id == row.id)
        ).all())
        return LiveEpisode(
            ref=row.ref or str(row.id),
            surface=Surface(row.surface),
            amount_paise=Paise(row.amount_paise),
            opened_at=row.opened_at,
            state=EpisodeState(row.state),
            diagnosed_cause=RootCause(row.root_cause) if row.root_cause else None,
            confidence=row.diagnosis_confidence or 0.0,
            provider_id=row.provider_id or "",
            recovered_paise=Paise(row.recovered_paise),
            closed_at=row.closed_at,
            terminal_reason=row.terminal_reason or "",
            seen_events=seen,
        )
