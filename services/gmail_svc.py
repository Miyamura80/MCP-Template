"""Gmail OAuth services - pure business logic.

Phase 3: connect / status / disconnect. Later phases extend this module
with the actual Gmail-API-backed services (compose/send/inbox/curate).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
from loguru import logger as log
from sqlalchemy.orm import Session

from common import global_config
from db.engine import use_db_session
from db.models.google_tokens import GoogleToken
from models.gmail import (
    GmailConnectInput,
    GmailConnectResult,
    GmailDisconnectInput,
    GmailDisconnectResult,
    GmailStatusInput,
    GmailStatusResult,
)
from services import service

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GMAIL_SCOPES: list[str] = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105
GOOGLE_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

_STATE_MAX_AGE_SECONDS = 10 * 60  # 10 minutes


# ---------------------------------------------------------------------------
# State signing (HMAC-SHA256 over JSON payload, key = SESSION_SECRET_KEY)
# ---------------------------------------------------------------------------


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _hmac_key() -> bytes:
    return global_config.SESSION_SECRET_KEY.encode("utf-8")


def _sign_state(user_id: str) -> str:
    payload = {
        "user_id": user_id,
        "nonce": secrets.token_urlsafe(16),
        "issued_at": int(time.time()),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(_hmac_key(), payload_bytes, hashlib.sha256).digest()
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(sig)}"


def _verify_state(state: str) -> str | None:
    """Return the embedded ``user_id`` if the state is valid + fresh, else None."""
    if not state or "." not in state:
        return None
    try:
        payload_b64, sig_b64 = state.split(".", 1)
        payload_bytes = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
    except (ValueError, binascii.Error):
        return None

    expected_sig = hmac.new(_hmac_key(), payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected_sig):
        return None

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    user_id = payload.get("user_id")
    issued_at = payload.get("issued_at")
    if not isinstance(user_id, str) or not isinstance(issued_at, int):
        return None
    if time.time() - issued_at > _STATE_MAX_AGE_SECONDS:
        return None
    return user_id


# ---------------------------------------------------------------------------
# DB helpers (shared with Phase 4)
# ---------------------------------------------------------------------------


@contextmanager
def _get_db_session() -> Generator[Session, None, None]:
    """Open a database session.

    Thin wrapper around ``db.engine.use_db_session`` so Phase 4 has a single
    obvious import to reach for.
    """
    with use_db_session() as session:
        yield session


def _load_token_row(session: Session, user_id: str) -> GoogleToken | None:
    """Return the active (non-revoked) GoogleToken row for a user, or None."""
    return (
        session.query(GoogleToken)
        .filter(GoogleToken.user_id == user_id, GoogleToken.revoked_at.is_(None))
        .one_or_none()
    )


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@service(
    name="gmail_connect",
    description="Begin the Google OAuth flow to link a Gmail account",
    input_model=GmailConnectInput,
    output_model=GmailConnectResult,
)
def gmail_connect(input: GmailConnectInput) -> GmailConnectResult:
    """Build the Google OAuth authorization URL for the user."""
    client_id = global_config.GOOGLE_CLIENT_ID
    redirect_uri = global_config.GOOGLE_REDIRECT_URI
    if not client_id:
        raise RuntimeError("Google OAuth not configured")
    if not redirect_uri:
        raise RuntimeError("Google OAuth not configured")

    state = _sign_state(input.user_id)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GMAIL_SCOPES),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    auth_url = f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"
    return GmailConnectResult(auth_url=auth_url, state=state)


@service(
    name="gmail_status",
    description="Return whether the user has a linked Gmail account",
    input_model=GmailStatusInput,
    output_model=GmailStatusResult,
)
def gmail_status(input: GmailStatusInput) -> GmailStatusResult:
    """Look up the current Gmail connection status for a user."""
    with _get_db_session() as session:
        row = _load_token_row(session, input.user_id)
        if row is None:
            return GmailStatusResult(connected=False)
        return GmailStatusResult(
            connected=True,
            email=row.email,
            scopes=list(row.scopes or []),
            granted_at=row.granted_at,
        )


@service(
    name="gmail_disconnect",
    description="Revoke and remove the user's linked Gmail account",
    input_model=GmailDisconnectInput,
    output_model=GmailDisconnectResult,
)
def gmail_disconnect(input: GmailDisconnectInput) -> GmailDisconnectResult:
    """Revoke the stored refresh token with Google + mark the row revoked.

    If the network revoke fails we still mark the row revoked locally so the
    user is never stuck in a half-connected state.
    """
    with _get_db_session() as session:
        row = _load_token_row(session, input.user_id)
        if row is None:
            return GmailDisconnectResult(revoked=False)

        # Best-effort decrypt + remote revoke. Failures here are non-fatal:
        # the row is still marked revoked locally below.
        try:
            from common.token_encryption import require_encryption

            enc = require_encryption()
            refresh_token = enc.decrypt(row.refresh_token_enc)
            httpx.post(
                GOOGLE_REVOKE_ENDPOINT,
                params={"token": refresh_token},
                timeout=10.0,
            )
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Google token revoke failed; revoking locally anyway: {}", exc)
        except Exception as exc:  # noqa: BLE001
            # Defensive boundary: decrypt() or require_encryption() may raise
            # provider-specific errors (cryptography.fernet.InvalidToken,
            # RuntimeError when key missing in prod). We MUST still mark the
            # row revoked locally so the user can recover.
            log.warning("Google revoke errored ({}): proceeding with local revoke", exc)

        row.revoked_at = datetime.now(UTC)
        session.commit()
        return GmailDisconnectResult(revoked=True)
