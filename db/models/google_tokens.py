"""Google OAuth tokens ORM model.

Stores one row per user holding an encrypted Google refresh token plus the
metadata needed to refresh an access token and surface connection status.
Access tokens are minted on demand and never persisted.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class GoogleToken(Base):
    __tablename__ = "google_tokens"

    # One Google connection per user_id
    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    refresh_token_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(
        String(32), nullable=False, default="v1"
    )
    scopes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=None)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
