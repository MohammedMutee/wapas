"""Initial schema, with an append-only audit table.

The interesting part of this migration is at the bottom: a trigger that makes
``audit_entry`` genuinely append-only at the storage layer. Application-level
immutability is a convention; a convention is not a guarantee, and the whole
point of a tamper-evident log is that it does not depend on the application
being well-behaved.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "counterparty",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("external_ref", sa.String(80), nullable=False, unique=True),
        sa.Column("is_business", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("display_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("phone", sa.String(20)),
        sa.Column("email", sa.String(200)),
        sa.Column("channel_consent", JSONB, nullable=False, server_default="[]"),
        sa.Column("on_dnd_registry", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("opted_out_at", TS),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_counterparty_external_ref", "counterparty", ["external_ref"])

    op.create_table(
        "risk_event",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("surface", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("counterparty_id", UUID, sa.ForeignKey("counterparty.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("occurred_at", TS, nullable=False),
        sa.Column("rail", sa.String(40)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_description", sa.Text()),
        sa.Column("error_source", sa.String(40)),
        sa.Column("error_step", sa.String(40)),
        sa.Column("error_reason", sa.String(80)),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("provider_ref", sa.String(80)),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("dedup_key", sa.String(200), nullable=False, unique=True),
        sa.Column("ingested_at", TS, nullable=False),
        # Money is integer paise. Enforce the sign at the storage layer too.
        sa.CheckConstraint("amount_paise >= 0", name="amount_non_negative"),
    )
    op.create_index("ix_risk_event_surface", "risk_event", ["surface"])
    op.create_index("ix_risk_event_kind", "risk_event", ["kind"])
    op.create_index("ix_risk_event_occurred_at", "risk_event", ["occurred_at"])
    op.create_index("ix_risk_event_dedup_key", "risk_event", ["dedup_key"])
    op.create_index("ix_risk_event_counterparty_id", "risk_event", ["counterparty_id"])

    op.create_table(
        "episode",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("risk_event_id", UUID, sa.ForeignKey("risk_event.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("counterparty_id", UUID, sa.ForeignKey("counterparty.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("arm", sa.String(40), nullable=False),
        sa.Column("surface", sa.String(40), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("root_cause", sa.String(40)),
        sa.Column("diagnosis_confidence", sa.Float()),
        sa.Column("p_recover_prior", sa.Float()),
        sa.Column("ev_paise", sa.BigInteger()),
        sa.Column("actions_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("contacts_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retries_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("spend_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("escalation_rung", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_retry_at", TS),
        sa.Column("last_rung_at", TS),
        sa.Column("active_promise_until", TS),
        sa.Column("dispute_open", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("capture_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("recovered_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("opened_at", TS, nullable=False),
        sa.Column("closed_at", TS),
        sa.Column("terminal_reason", sa.String(120)),
        sa.Column("seed", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint("recovered_paise >= 0", name="recovered_non_negative"),
        sa.CheckConstraint("cost_paise >= 0", name="cost_non_negative"),
        sa.CheckConstraint("contacts_used >= 0 AND actions_used >= 0",
                           name="counters_non_negative"),
    )
    op.create_index("ix_episode_state", "episode", ["state"])
    op.create_index("ix_episode_arm", "episode", ["arm"])
    op.create_index("ix_episode_surface", "episode", ["surface"])
    op.create_index("ix_episode_root_cause", "episode", ["root_cause"])
    op.create_index("ix_episode_opened_at", "episode", ["opened_at"])
    op.create_index("ix_episode_arm_state", "episode", ["arm", "state"])
    op.create_index("ix_episode_cause_arm", "episode", ["root_cause", "arm"])
    op.create_index("ix_episode_risk_event_id", "episode", ["risk_event_id"])
    op.create_index("ix_episode_counterparty_id", "episode", ["counterparty_id"])

    op.create_table(
        "decision",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("episode_id", UUID, sa.ForeignKey("episode.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("node", sa.String(40), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("proposed_action", JSONB),
        sa.Column("gate_verdict", sa.String(40)),
        sa.Column("gate_reasons", JSONB, nullable=False, server_default="[]"),
        sa.Column("policy_version", sa.String(80), nullable=False, server_default=""),
        sa.Column("llm_provider", sa.String(40)),
        sa.Column("llm_model", sa.String(120)),
        sa.Column("llm_mode", sa.String(20)),
        sa.Column("prompt_hash", sa.String(64)),
        sa.Column("llm_response", JSONB),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("llm_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("uq_decision_step", "decision", ["episode_id", "step_no"], unique=True)
    op.create_index("ix_decision_node", "decision", ["node"])
    op.create_index("ix_decision_gate_verdict", "decision", ["gate_verdict"])
    op.create_index("ix_decision_prompt_hash", "decision", ["prompt_hash"])
    op.create_index("ix_decision_episode_id", "decision", ["episode_id"])

    op.create_table(
        "action",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("decision_id", UUID, sa.ForeignKey("decision.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("episode_id", UUID, sa.ForeignKey("episode.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("tool", sa.String(40), nullable=False),
        sa.Column("args", JSONB, nullable=False),
        sa.Column("channel", sa.String(40)),
        sa.Column("rung", sa.Integer()),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("scheduled_for", TS),
        sa.Column("executed_at", TS),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("provider_ref", sa.String(120)),
        sa.Column("provider_response", JSONB),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_action_tool", "action", ["tool"])
    op.create_index("ix_action_status", "action", ["status"])
    op.create_index("ix_action_scheduled_for", "action", ["scheduled_for"])
    op.create_index("ix_action_idempotency_key", "action", ["idempotency_key"])
    op.create_index("ix_action_decision_id", "action", ["decision_id"])
    op.create_index("ix_action_episode_id", "action", ["episode_id"])

    op.create_table(
        "cost_entry",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("episode_id", UUID, sa.ForeignKey("episode.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("action_id", UUID, sa.ForeignKey("action.id", ondelete="CASCADE")),
        sa.Column("decision_id", UUID, sa.ForeignKey("decision.id", ondelete="CASCADE")),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("unit_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rate_ref", sa.String(80), nullable=False, server_default=""),
        sa.Column("notional", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint("amount_paise >= 0", name="cost_non_negative"),
    )
    op.create_index("ix_cost_entry_kind", "cost_entry", ["kind"])
    op.create_index("ix_cost_entry_episode_id", "cost_entry", ["episode_id"])

    op.create_table(
        "outcome",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("episode_id", UUID, sa.ForeignKey("episode.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("occurred_at", TS, nullable=False),
        sa.Column("attributed_to_action_id", UUID,
                  sa.ForeignKey("action.id", ondelete="SET NULL")),
        sa.Column("attribution_method", sa.String(40), nullable=False),
        sa.Column("detail", JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_outcome_kind", "outcome", ["kind"])
    op.create_index("ix_outcome_occurred_at", "outcome", ["occurred_at"])
    op.create_index("ix_outcome_episode_id", "outcome", ["episode_id"])

    op.create_table(
        "audit_entry",
        sa.Column("seq", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("at", TS, nullable=False),
        sa.Column("episode_id", UUID, sa.ForeignKey("episode.id", ondelete="RESTRICT")),
        sa.Column("actor", sa.String(40), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False, unique=True),
    )
    op.create_index("ix_audit_entry_at", "audit_entry", ["at"])
    op.create_index("ix_audit_entry_actor", "audit_entry", ["actor"])
    op.create_index("ix_audit_entry_event_type", "audit_entry", ["event_type"])
    op.create_index("ix_audit_entry_episode_id", "audit_entry", ["episode_id"])

    # ── the append-only guarantee ────────────────────────────────────────────
    # Immutability enforced by the database, not by application discipline.
    # Note the FK above is ON DELETE RESTRICT, not CASCADE: deleting an episode
    # must not be a back door to deleting its audit trail.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION wapas_audit_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'audit_entry is append-only: % on seq=% is refused',
                TG_OP, COALESCE(OLD.seq, NEW.seq)
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_entry_no_update
            BEFORE UPDATE ON audit_entry
            FOR EACH ROW EXECUTE FUNCTION wapas_audit_append_only();
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_entry_no_delete
            BEFORE DELETE ON audit_entry
            FOR EACH ROW EXECUTE FUNCTION wapas_audit_append_only();
        """
    )
    # TRUNCATE bypasses row-level triggers, so it needs its own statement-level one.
    op.execute(
        """
        CREATE TRIGGER audit_entry_no_truncate
            BEFORE TRUNCATE ON audit_entry
            FOR EACH STATEMENT EXECUTE FUNCTION wapas_audit_append_only();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_entry_no_truncate ON audit_entry")
    op.execute("DROP TRIGGER IF EXISTS audit_entry_no_delete ON audit_entry")
    op.execute("DROP TRIGGER IF EXISTS audit_entry_no_update ON audit_entry")
    op.execute("DROP FUNCTION IF EXISTS wapas_audit_append_only()")
    for table in ("audit_entry", "outcome", "cost_entry", "action", "decision",
                  "episode", "risk_event", "counterparty"):
        op.drop_table(table)
