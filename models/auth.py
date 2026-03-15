"""Pydantic schemas for API-key management."""

from datetime import datetime

from pydantic import BaseModel


class CreateAPIKeyRequest(BaseModel):
    name: str = "Default"
    expires_in_days: int | None = None


class CreateAPIKeyResponse(BaseModel):
    key: str
    key_prefix: str
    name: str
    expires_at: datetime | None = None


class APIKeyInfo(BaseModel):
    id: int
    key_prefix: str
    name: str
    revoked: bool
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime
