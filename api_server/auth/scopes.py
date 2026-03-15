"""API key scope definitions and validation."""

from fastapi import Depends, HTTPException

from api_server.auth.unified_auth import AuthenticatedUser, get_authenticated_user

# Scope constants
SERVICES_READ = "services:read"
SERVICES_EXECUTE = "services:execute"
ADMIN_READ = "admin:read"
ADMIN_WRITE = "admin:write"

ALL_SCOPES = frozenset({SERVICES_READ, SERVICES_EXECUTE, ADMIN_READ, ADMIN_WRITE})

# Scope templates
SCOPE_TEMPLATES: dict[str, list[str]] = {
    "read_only": [SERVICES_READ],
    "standard": [SERVICES_READ, SERVICES_EXECUTE],
    "admin": ["*"],
}


def validate_scopes(scopes: list[str]) -> list[str]:
    """Expand templates and validate scope strings.

    Returns the resolved list of scopes.
    Raises ``ValueError`` for unknown scopes.
    """
    resolved: list[str] = []
    for s in scopes:
        if s in SCOPE_TEMPLATES:
            resolved.extend(SCOPE_TEMPLATES[s])
        elif (
            s == "*"
            or s in ALL_SCOPES
            or (s.endswith(":*") and s.split(":")[0] in {"services", "admin"})
        ):
            resolved.append(s)
        else:
            raise ValueError(f"Unknown scope: {s!r}")
    return sorted(set(resolved))


def check_scopes(required: list[str], granted: list[str] | None) -> bool:
    """Check whether *granted* scopes satisfy all *required* scopes.

    ``None`` means legacy key with no scope restrictions (allow all).
    Supports wildcards: ``*`` (everything) and ``resource:*`` (all in resource).
    """
    if granted is None:
        return True  # Legacy key - no restrictions
    if "*" in granted:
        return True
    for req in required:
        parts = req.split(":")
        resource_wildcard = f"{parts[0]}:*" if len(parts) > 1 else None
        if req not in granted and (
            resource_wildcard is None or resource_wildcard not in granted
        ):
            return False
    return True


def require_scopes(*scopes: str):
    """FastAPI dependency factory that enforces scope requirements."""

    def _check(
        user: AuthenticatedUser = Depends(get_authenticated_user),
    ) -> AuthenticatedUser:
        if not check_scopes(list(scopes), user.scopes):
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient scopes. Required: {list(scopes)}",
            )
        return user

    return _check
