"""Well-known discovery documents for the /mcp endpoint.

Two documents live here:

* **OAuth 2.0 Protected Resource Metadata** (RFC 9728) - tells MCP clients
  where the authorization server is. Required of resource servers by the MCP
  spec (2025-11-25). Served at the path-form URI
  (``/.well-known/oauth-protected-resource/mcp``, tried first because the MCP
  endpoint lives at ``/mcp``) and the root form. Returns 404 when OAuth is not
  configured, so unauthenticated discovery cleanly signals "no authorization
  server" instead of advertising a broken flow.

* **MCP Server Card** (SEP-2127) - pre-connect *branding*: the name, title,
  description, and icon a registry or client shows before anyone connects.
  Always available (branding has no auth dependency) and served with
  ``Access-Control-Allow-Origin: *`` so any registry crawler can read it.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from api_server.auth.authkit_auth import authkit_domain, mcp_resource_url
from common import global_config
from common.config_models import IconConfig

router = APIRouter(tags=["well-known"])

SERVER_CARD_SCHEMA = (
    "https://static.modelcontextprotocol.io/schemas/v1/server-card.schema.json"
)


def _server_version() -> str:
    """Resolve the published package version; fall back when not installed."""
    try:
        return _pkg_version("mcp-template")
    except PackageNotFoundError:
        return "0.0.0"


def _icon(icon: IconConfig) -> dict:
    out: dict = {"src": icon.src, "mimeType": icon.mime_type, "sizes": icon.sizes}
    if icon.theme:
        out["theme"] = icon.theme
    return out


@router.get("/.well-known/mcp/server-card.json")
def mcp_server_card() -> JSONResponse:
    """SEP-2127 Server Card - pre-connect registry/client branding."""
    b = global_config.branding
    card = {
        "$schema": SERVER_CARD_SCHEMA,
        "name": b.name,
        "version": _server_version(),
        "title": b.title,
        "description": b.description,
        "websiteUrl": b.website_url,
        "repository": {"url": b.repository_url, "source": b.repository_source},
        "icons": [_icon(i) for i in b.icons],
        "remotes": [{"type": "streamable-http", "url": mcp_resource_url()}],
    }
    # Public branding: any registry crawler (cross-origin) must be able to read it.
    return JSONResponse(card, headers={"Access-Control-Allow-Origin": "*"})


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
