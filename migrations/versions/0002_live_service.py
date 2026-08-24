"""Live service: episode ref, provider id, and webhook delivery de-duplication.

**Hand-written, and it has to be.** `alembic revision --autogenerate` produced a
migration that added the table above and, in the same breath, dropped five
check constraints and rewrote three unique constraints as indexes:

    op.drop_constraint('ck_episode_recovered_non_negative', 'episode', type_='check')
    op.drop_constraint('ck_episode_cost_non_negative', 'episode', type_='check')
    op.drop_constraint('ck_risk_event_amount_non_negative', 'risk_event', type_='check')
    ...

Those are the guarantees that money cannot go negative. They exist because
`0001_initial_schema` declares them in raw SQL, which the ORM models do not
mirror, so autogenerate sees them as drift and removes them. The diff reads
like "adds a table" and is in fact "adds a table and disables the accounting
invariants", which is the most dangerous shape a migration can have.

Revision ID: 0002_live_service
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002_live_service"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The merchant's own reference. Unique, because the same reference arriving
    # twice means the same episode and only the database can enforce that
    # across processes.
    op.add_column("episode", sa.Column("ref", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_episode_ref", "episode", ["ref"])

    # A webhook often carries only the provider's identifier, not ours.
    op.add_column("episode", sa.Column("provider_id", sa.String(length=64), nullable=True))
    op.create_index("ix_episode_provider_id", "episode", ["provider_id"])

    # Exists for its unique constraint. Providers retry until they get a 2xx, so
    # the same event arrives again whenever a response is slow or a worker dies,
    # and applying it twice credits money that arrived once.
    op.create_table(
        "webhook_delivery",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("episode_id", sa.Uuid(), nullable=False),
        sa.Column("event_identity", sa.String(length=200), nullable=False),
        sa.Column("event", sa.String(length=60), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["episode_id"], ["episode.id"],
            name="fk_webhook_delivery_episode_id_episode", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_webhook_delivery"),
        sa.UniqueConstraint("episode_id", "event_identity", name="uq_delivery_identity"),
        sa.CheckConstraint("amount_paise >= 0", name="ck_delivery_amount_non_negative"),
    )
    op.create_index("ix_webhook_delivery_episode_id", "webhook_delivery", ["episode_id"])
    op.create_index("ix_webhook_delivery_received_at", "webhook_delivery", ["received_at"])


def downgrade() -> None:
    op.drop_index("ix_webhook_delivery_received_at", table_name="webhook_delivery")
    op.drop_index("ix_webhook_delivery_episode_id", table_name="webhook_delivery")
    op.drop_table("webhook_delivery")
    op.drop_index("ix_episode_provider_id", table_name="episode")
    op.drop_column("episode", "provider_id")
    op.drop_constraint("uq_episode_ref", "episode", type_="unique")
    op.drop_column("episode", "ref")
