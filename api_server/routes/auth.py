"""API key CRUD and ``/me`` endpoint."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api_server.auth import AuthenticatedUser, get_authenticated_user
from api_server.auth.api_key_auth import create_api_key, revoke_api_key
from api_server.auth.scopes import SCOPE_TEMPLATES, check_scopes, validate_scopes
from db.engine import get_db_session
from db.models.api_keys import APIKey
from models.auth import APIKeyInfo, CreateAPIKeyRequest, CreateAPIKeyResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/me")
def me(user: AuthenticatedUser = Depends(get_authenticated_user)):
    return {
        "user_id": user.user_id,
        "email": user.email,
        "auth_method": user.auth_method,
    }


@router.post("/api-keys", response_model=CreateAPIKeyResponse)
def create_key(
    body: CreateAPIKeyRequest,
    user: AuthenticatedUser = Depends(get_authenticated_user),
    session: Session = Depends(get_db_session),
):
    # Resolve scopes from template or explicit list (reject ambiguous requests)
    if body.scope_template and body.scopes is not None:
        raise HTTPException(
            status_code=400,
            detail="Provide either 'scope_template' or 'scopes', not both.",
        )
    scopes: list[str] | None = None
    if body.scope_template:
        if body.scope_template not in SCOPE_TEMPLATES:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown scope template: {body.scope_template!r}. "
                f"Available: {list(SCOPE_TEMPLATES.keys())}",
            )
        scopes = validate_scopes([body.scope_template])
    elif body.scopes is not None:
        try:
            scopes = validate_scopes(body.scopes, allow_templates=False)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Prevent privilege escalation: new key's scopes must be a subset of
    # the caller's own scopes. Treat legacy keys (scopes=None) as ["*"]
    # so they can create any scoped key within their implicit full access.
    # An unscoped (legacy) key grants unrestricted access, so a scoped
    # caller must not be able to create one.
    caller_scopes = user.scopes if user.scopes is not None else ["*"]
    if scopes is None and "*" not in caller_scopes:
        raise HTTPException(
            status_code=403,
            detail="Cannot create an unrestricted key from a scoped key.",
        )
    if scopes is not None and not check_scopes(scopes, caller_scopes):
        raise HTTPException(
            status_code=403,
            detail="Cannot create a key with scopes that exceed your own.",
        )

    raw_key, row = create_api_key(
        session,
        user_id=user.user_id,
        name=body.name,
        expires_in_days=body.expires_in_days,
        email=user.email,
        scopes=scopes,
    )
    return CreateAPIKeyResponse(
        key=raw_key,
        key_prefix=row.key_prefix,
        name=row.name,
        scopes=row.scopes,
        expires_at=row.expires_at,
    )


@router.get("/api-keys", response_model=list[APIKeyInfo])
def list_keys(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    session: Session = Depends(get_db_session),
):
    rows = (
        session.query(APIKey)
        .filter_by(user_id=user.user_id, revoked=False)
        .order_by(APIKey.created_at.desc())
        .all()
    )
    return [
        APIKeyInfo(
            id=r.id,
            key_prefix=r.key_prefix,
            name=r.name,
            scopes=r.scopes,
            revoked=r.revoked,
            expires_at=r.expires_at,
            last_used_at=r.last_used_at,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.delete("/api-keys/{key_id}")
def delete_key(
    key_id: int,
    user: AuthenticatedUser = Depends(get_authenticated_user),
    session: Session = Depends(get_db_session),
):
    ok = revoke_api_key(session, key_id=key_id, user_id=user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"status": "revoked"}
