"""Pydantic schemas for API-key management."""

from datetime import datetime

from pydantic import BaseModel, Field

# One policy for what a valid key request is, whichever surface makes it
# (REST /api/v1/auth/api-keys, the dashboard's create page).
MAX_KEY_NAME = 100
MAX_KEY_DAYS = 365


class CreateAPIKeyRequest(BaseModel):
    name: str = Field(default="Default", min_length=1, max_length=MAX_KEY_NAME)
    expires_in_days: int | None = Field(default=None, ge=1, le=MAX_KEY_DAYS)
    scopes: list[str] | None = None
    scope_template: str | None = None


class CreateAPIKeyResponse(BaseModel):
    key: str
    key_prefix: str
    name: str
    scopes: list[str] | None = None
    expires_at: datetime | None = None


class APIKeyInfo(BaseModel):
    id: int
    key_prefix: str
    name: str
    scopes: list[str] | None = None
    revoked: bool
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime
