"""Well-known discovery documents for the /mcp endpoint.

Two documents live here:

* **OAuth 2.0 Protected Resource Metadata** (RFC 9728) - tells MCP clients
  where the authorization server is. Required of resource servers by the MCP
  spec (2025-11-25). Served at the path-form URI
  (``/.well-known/oauth-protected-resource/mcp``, tried first because the MCP
  endpoint lives at ``/mcp``) and the root form. Returns 404 when OAuth is not
  configured, so unauthenticated discovery cleanly signals "no authorization
  server" instead of advertising a broken flow.

* **OAuth 2.0 Authorization Server Metadata** (RFC 8414) - the authorization
  server in this template is AuthKit, which publishes its own RFC 8414 document
  at its issuer origin. The spec-correct path is PRM (above) -> AuthKit, but in
  practice many MCP clients and registry crawlers probe
  ``/.well-known/oauth-authorization-server`` against the *resource server*
  origin directly. We redirect those requests to AuthKit's authoritative
  metadata so the auth flow bootstraps regardless of which origin a client
  probes. Returns 404 when OAuth is not configured, mirroring the PRM endpoints.

* **MCP Server Card** (SEP-2127) - pre-connect *branding*: the name, title,
  description, and icon a registry or client shows before anyone connects.
  Always available (branding has no auth dependency) and served with
  ``Access-Control-Allow-Origin: *`` so any registry crawler can read it.

* **A2A Agent Card** (Agent2Agent spec v0.3.0) - the agent-protocol analogue of
  the Server Card. Served at ``/.well-known/agent-card.json`` so A2A clients and
  orchestrators can discover this agent's identity, endpoint, and skills. Built
  from the same branding config plus the shared service registry (each service
  becomes an A2A skill). Public and cross-origin readable, like the Server Card.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse

from api_server.auth.authkit_auth import authkit_domain, mcp_resource_url
from common import global_config
from common.config_models import BrandingConfig, IconConfig
from models.a2a import (
    A2AAgentCapabilities,
    A2AAgentCard,
    A2AAgentProvider,
    A2AAgentSkill,
)
from services import discover_services, get_registry

router = APIRouter(tags=["well-known"])


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


def _agent_endpoint_url(b: BrandingConfig) -> str:
    """Resolve the public host to advertise as the agent's ``url``.

    A2A requires ``url`` (and per spec the host it points to must serve the
    declared ``preferredTransport``). This template ships the *discovery card*
    only - the live A2A JSON-RPC transport is a tracked follow-up - so we point
    at the public API host where that transport will mount, preferring a
    configured public host over the branding website and never the localhost dev
    default (which would point clients at a dead endpoint).
    """
    return (
        global_config.API_PUBLIC_URL or global_config.MCP_PUBLIC_URL or b.website_url
    ).rstrip("/")


def _service_skills() -> list[A2AAgentSkill]:
    """Map each registered service onto an A2A skill (id == service name)."""
    discover_services()
    return [
        A2AAgentSkill(
            id=entry.name,
            name=entry.name,
            description=entry.description,
            tags=["mcp"],
            input_modes=["application/json"],
            output_modes=["application/json"],
        )
        for entry in get_registry()
    ]


@router.get("/.well-known/agent-card.json")
def a2a_agent_card() -> JSONResponse:
    """A2A Agent Card (spec v0.3.0) - pre-connect agent discovery document.

    Discovery only: it advertises this agent's identity and skills so A2A
    registries/clients can find it. The backing A2A transport at ``url`` is a
    tracked follow-up, so the declared ``preferredTransport`` is aspirational
    until that lands.
    """
    b = global_config.branding
    card = A2AAgentCard(
        name=b.title,
        description=b.description,
        url=_agent_endpoint_url(b),
        version=_server_version(),
        capabilities=A2AAgentCapabilities(),
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json", "text/plain"],
        skills=_service_skills(),
        # JSONRPC is A2A's default/simplest binding. We advertise it (rather than
        # the HTTP+JSON REST binding) because the live A2A transport - whichever
        # binding it lands on - is a tracked follow-up; the card is discovery only.
        preferred_transport="JSONRPC",
        provider=A2AAgentProvider(organization=b.title, url=b.website_url),
        icon_url=b.icons[0].src if b.icons else None,
        documentation_url=b.website_url,
    )
    # Public discovery document: any A2A crawler (cross-origin) must read it.
    return JSONResponse(card.to_wire(), headers={"Access-Control-Allow-Origin": "*"})


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


def _authorization_server_redirect() -> RedirectResponse:
    domain = authkit_domain()
    if not domain:
        raise HTTPException(status_code=404, detail="OAuth is not configured")
    # 307 keeps the request a GET (metadata is always fetched with GET) while
    # signaling a temporary redirect, since the target depends on configuration.
    return RedirectResponse(
        url=f"{domain}/.well-known/oauth-authorization-server", status_code=307
    )


@router.get("/.well-known/oauth-authorization-server/mcp")
def authorization_server_metadata_for_mcp() -> RedirectResponse:
    return _authorization_server_redirect()


@router.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata_root() -> RedirectResponse:
    return _authorization_server_redirect()
