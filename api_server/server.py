"""FastAPI application - CORS, session middleware, route registration."""

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from api_server.middleware.error_handler import (
    ErrorHandlerMiddleware,
    RequestIdMiddleware,
)
from api_server.middleware.mcp_auth import MCPAuthMiddleware
from api_server.middleware.rate_limit import RateLimitMiddleware
from api_server.routes import agentic_payments, auth, health, services
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
app.include_router(services.router)
app.include_router(auth.router)
app.include_router(checkout.router)
app.include_router(metering.router)
app.include_router(subscription.router)
app.include_router(webhooks.router)
app.include_router(agentic_payments.router)

# --- MCP server (streamable HTTP) -----------------------------------------
# Mounts FastMCP at /mcp so CLI/API/MCP share one process, port, and middleware.
mount_mcp_server(app)


def main() -> None:
    """Entry-point for ``mymcp-api`` console script."""
    uvicorn.run(
        "api_server.server:app",
        host=global_config.server.host,
        port=global_config.server.port,
        reload=False,
    )
