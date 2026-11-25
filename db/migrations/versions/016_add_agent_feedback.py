"""Add agent_feedback for the agent-facing ``feedback`` tool.

One row per submission; outbound sinks (Slack, ...) fan out from here on a
best-effort basis. See ``db/models/feedback.py``.

Revision ID: 016
Revises: 015
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_feedback",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_feedback_user_created",
        "agent_feedback",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_feedback_user_created", table_name="agent_feedback")
    op.drop_table("agent_feedback")
