"""Add last_stripe_event_id to user_subscriptions for webhook dedup.

Revision ID: 004
Revises: 003
Create Date: 2026-03-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_subscriptions",
        sa.Column("last_stripe_event_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_subscriptions", "last_stripe_event_id")
