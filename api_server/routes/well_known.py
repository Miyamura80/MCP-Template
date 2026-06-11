"""RFC 9728 OAuth 2.0 Protected Resource Metadata for the /mcp endpoint.

MCP clients discover the authorization server here (MCP spec 2025-11-25
requires resource servers to publish this document). Served both at the
path-form URI (``/.well-known/oauth-protected-resource/mcp``, tried first by
clients because the MCP endpoint lives at ``/mcp``) and at the root form.

Returns 404 when OAuth is not configured, so unauthenticated discovery cleanly
signals "no authorization server" instead of advertising a broken flow.
"""

from fastapi import APIRouter, HTTPException

from api_server.auth.authkit_auth import authkit_domain, mcp_resource_url

router = APIRouter(tags=["well-known"])


def _metadata() -> dict:
    domain = authkit_domain()
    if not domain:
        raise HTTPException(status_code=404, detail="OAuth is not configured")
    return {
        "resource": mcp_resource_url(),
        "authorization_servers": [domain],
        "bearer_methods_supported": ["header"],
    }


@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource_metadata_for_mcp() -> dict:
    return _metadata()


@router.get("/.well-known/oauth-protected-resource")
def protected_resource_metadata_root() -> dict:
    return _metadata()
