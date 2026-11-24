"""Add approval_settings and pending_actions for dangerous-tool approval.

See ``db/models/approvals.py``.

Revision ID: 017
Revises: 016
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "017"
down_revision: str | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_settings",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("overrides", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "pending_actions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("tool", sa.String(length=128), nullable=False),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("args_hash", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pending_actions_user_status", "pending_actions", ["user_id", "status"]
    )
    op.create_index(
        "ix_pending_actions_user_call",
        "pending_actions",
        ["user_id", "tool", "args_hash"],
    )
    active = sa.text("status IN ('pending', 'approved', 'executing')")
    op.create_index(
        "uq_pending_actions_active_call",
        "pending_actions",
        ["user_id", "tool", "args_hash"],
        unique=True,
        sqlite_where=active,
        postgresql_where=active,
    )


def downgrade() -> None:
    op.drop_index("uq_pending_actions_active_call", table_name="pending_actions")
    op.drop_index("ix_pending_actions_user_call", table_name="pending_actions")
    op.drop_index("ix_pending_actions_user_status", table_name="pending_actions")
    op.drop_table("pending_actions")
    op.drop_table("approval_settings")
