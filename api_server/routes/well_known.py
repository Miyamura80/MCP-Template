"""Well-known discovery documents for the /mcp endpoint.

Three documents live here:

* **OAuth 2.0 Protected Resource Metadata** (RFC 9728) - tells MCP clients
  where the authorization server is. Required of resource servers by the MCP
  spec (2025-11-25). Served at the path-form URI
  (``/.well-known/oauth-protected-resource/mcp``, tried first because the MCP
  endpoint lives at ``/mcp``) and the root form. Returns 404 when OAuth is not
  configured, so unauthenticated discovery cleanly signals "no authorization
  server" instead of advertising a broken flow.

* **OAuth 2.0 Authorization Server Metadata** (RFC 8414) - mirrors the
  authorization server's (AuthKit's) ``issuer`` / ``authorization_endpoint`` /
  ``token_endpoint`` document at *this* resource server's well-known path.
  Compliant clients follow the RFC 9728 pointer above to the AS and read its
  metadata there, so strictly this is *not* the canonical discovery path. But
  many MCP clients and registry scanners look for RFC 8414 metadata directly on
  the resource server and do **not** follow a redirect to the AS - they then
  report "no OAuth metadata available". An earlier revision answered this path
  with a 307 redirect to AuthKit, but the scanners that motivated it do not
  follow the redirect; serving the document inline as a 200 is what actually
  satisfies them. The upstream document is fetched once and cached.

* **MCP Server Card** (SEP-2127) - pre-connect *branding*: the name, title,
  description, and icon a registry or client shows before anyone connects.
  Always available (branding has no auth dependency) and served with
  ``Access-Control-Allow-Origin: *`` so any registry crawler can read it.
"""

import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from api_server.auth.authkit_auth import authkit_domain, mcp_resource_url
from common import global_config
from common.config_models import IconConfig

router = APIRouter(tags=["well-known"])

# AuthKit's RFC 8414 document is effectively static; cache per issuer so the
# resource server does not make an outbound call on every discovery request.
_AS_METADATA_TTL_SECONDS = 3600.0
_as_metadata_cache: dict[str, tuple[float, dict]] = {}


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
    """SEP-2127 Server Card - pre-connect registry/client branding.

    No ``$schema`` is emitted: the draft SEP-2127 server-card schema is not yet
    published (the URL 404s), so advertising it would only break validators.
    """
    b = global_config.branding
    card: dict = {
        "name": b.name,
        "version": _server_version(),
        "title": b.title,
        "description": b.description,
        "websiteUrl": b.website_url,
        "repository": {"url": b.repository_url, "source": b.repository_source},
        "icons": [_icon(i) for i in b.icons],
    }
    # Only advertise a remote when a real public URL is configured. mcp_resource_url()
    # falls back to localhost when MCP_PUBLIC_URL is unset (e.g. a deployed no-OAuth
    # server), and publishing localhost would point registries at a dead endpoint.
    public_url = global_config.MCP_PUBLIC_URL
    if public_url:
        card["remotes"] = [{"type": "streamable-http", "url": public_url.rstrip("/")}]
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


def _authorization_server_metadata() -> dict:
    """Return AuthKit's RFC 8414 metadata document, cached per issuer.

    404 when OAuth is unconfigured (mirrors the PRM routes); 502 when the
    upstream authorization server cannot be reached, so callers see a clear
    "try again" signal rather than a cached or partial document.
    """
    domain = authkit_domain()
    if not domain:
        raise HTTPException(status_code=404, detail="OAuth is not configured")

    now = time.monotonic()
    cached = _as_metadata_cache.get(domain)
    if cached and now - cached[0] < _AS_METADATA_TTL_SECONDS:
        return cached[1]

    url = f"{domain}/.well-known/oauth-authorization-server"
    try:
        resp = httpx.get(url, timeout=5.0, follow_redirects=True)
        resp.raise_for_status()
        metadata = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # ValueError covers a non-JSON body (json.JSONDecodeError subclasses it);
        # treat a malformed upstream document the same as a fetch failure.
        raise HTTPException(
            status_code=502,
            detail="Failed to fetch authorization server metadata",
        ) from exc

    _as_metadata_cache[domain] = (now, metadata)
    return metadata


@router.get("/.well-known/oauth-authorization-server/mcp")
def authorization_server_metadata_for_mcp() -> JSONResponse:
    # Public discovery: registry/scanner crawlers read this cross-origin.
    return JSONResponse(
        _authorization_server_metadata(),
        headers={"Access-Control-Allow-Origin": "*"},
    )


@router.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata_root() -> JSONResponse:
    return JSONResponse(
        _authorization_server_metadata(),
        headers={"Access-Control-Allow-Origin": "*"},
    )
