"""FastAPI application - CORS, session middleware, route registration."""

from typing import Any
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from starlette.middleware.sessions import SessionMiddleware

from api_server.middleware.error_handler import (
    ErrorHandlerMiddleware,
    RequestIdMiddleware,
)
from api_server.middleware.mcp_auth import MCPAuthMiddleware
from api_server.middleware.rate_limit import RateLimitMiddleware
from api_server.routes import (
    agentic_payments,
    auth,
    google_oauth,
    health,
    services,
    well_known,
)
from api_server.routes.payments import checkout, metering, subscription, webhooks
from common import global_config
from mcp_server.server import lifespan as mcp_lifespan
from mcp_server.server import mount_on as mount_mcp_server

app = FastAPI(title="mymcp-api", version="0.1.0", lifespan=mcp_lifespan)

# --- Middleware (last-added = outermost in Starlette) ---------------------

app.add_middleware(ErrorHandlerMiddleware)  # type: ignore[arg-type]
app.add_middleware(RateLimitMiddleware)  # type: ignore[arg-type]
app.add_middleware(RequestIdMiddleware)  # type: ignore[arg-type]

# Pure ASGI middleware that only acts on /mcp; sits outside RequestId/RateLimit
# so authenticated SSE streams are not buffered through BaseHTTPMiddleware.
app.add_middleware(MCPAuthMiddleware)  # type: ignore[arg-type]

app.add_middleware(
    SessionMiddleware,  # type: ignore[arg-type]
    secret_key=global_config.SESSION_SECRET_KEY,
)

app.add_middleware(
    CORSMiddleware,  # type: ignore[arg-type]
    allow_origins=global_config.server.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routes ---------------------------------------------------------------

app.include_router(health.router)
app.include_router(well_known.router)
app.include_router(services.router)
app.include_router(auth.router)
app.include_router(google_oauth.router)
app.include_router(checkout.router)
app.include_router(metering.router)
app.include_router(subscription.router)
app.include_router(webhooks.router)
app.include_router(agentic_payments.router)


# --- OpenAPI publication --------------------------------------------------
# FastAPI serves the spec at /openapi.json, but without an absolute ``servers``
# entry an agent/scanner that fetches it can't tell where to send requests and
# may report the API as undiscoverable. Inject the public origin (derived from
# MCP_PUBLIC_URL, falling back to the local bind) and alias the legacy
# /swagger.json path that older scanners probe.
#
# Registered before mount_mcp_server() because the MCP app is mounted at "/"
# and would otherwise shadow the /swagger.json route.


def _public_api_base() -> str:
    """Absolute public origin for the API.

    Derived from ``MCP_PUBLIC_URL`` (the canonical /mcp resource URL) by
    stripping its path, so the OpenAPI ``servers`` entry matches the host
    clients actually reach. Falls back to the local bind in dev.
    """
    configured = global_config.MCP_PUBLIC_URL
    if configured:
        parts = urlsplit(configured.rstrip("/"))
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    return f"http://localhost:{global_config.server.port}"


def custom_openapi() -> dict[str, Any]:
    """OpenAPI schema with an absolute ``servers`` block for discoverability."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    schema["servers"] = [{"url": _public_api_base(), "description": "API server"}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]


@app.get("/swagger.json", include_in_schema=False)
def swagger_json() -> dict[str, Any]:
    """Alias of /openapi.json for scanners that probe the legacy path."""
    return app.openapi()


# --- MCP server (streamable HTTP) -----------------------------------------
# Mounts FastMCP at /mcp so CLI/API/MCP share one process, port, and middleware.
# Must come last: it mounts at "/" and shadows any route registered after it.
mount_mcp_server(app)


def main() -> None:
    """Entry-point for ``mymcp-api`` console script."""
    uvicorn.run(
        "api_server.server:app",
        host=global_config.server.host,
        port=global_config.server.port,
        reload=False,
    )
