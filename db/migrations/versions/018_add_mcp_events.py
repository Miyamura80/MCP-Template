"""Add event_cursors, event_log and event_subscriptions for MCP Events; channel on deliveries.

See ``db/models/events.py``.

Revision ID: 018
Revises: 017
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "018"
down_revision: str | None = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_cursors",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("pruned_through", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "event_log",
        sa.Column("seq", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_seq", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("seq"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index("ix_event_log_created_at", "event_log", ["created_at"])
    op.create_index(
        "uq_event_log_user_seq", "event_log", ["user_id", "user_seq"], unique=True
    )
    op.create_index(
        "ix_event_log_user_name_seq", "event_log", ["user_id", "name", "user_seq"]
    )
    op.create_table(
        "event_subscriptions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("secret_enc", sa.LargeBinary(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_event_subscriptions_user_id", "event_subscriptions", ["user_id"]
    )
    op.create_index(
        "ix_event_subscriptions_user_name", "event_subscriptions", ["user_id", "name"]
    )
    with op.batch_alter_table("webhook_deliveries") as batch:
        batch.add_column(
            sa.Column(
                "channel",
                sa.String(length=16),
                nullable=False,
                server_default="webhook",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("webhook_deliveries") as batch:
        batch.drop_column("channel")
    op.drop_index("ix_event_subscriptions_user_name", table_name="event_subscriptions")
    op.drop_index("ix_event_subscriptions_user_id", table_name="event_subscriptions")
    op.drop_table("event_subscriptions")
    op.drop_index("ix_event_log_user_name_seq", table_name="event_log")
    op.drop_index("uq_event_log_user_seq", table_name="event_log")
    op.drop_index("ix_event_log_created_at", table_name="event_log")
    op.drop_table("event_log")
    op.drop_table("event_cursors")
