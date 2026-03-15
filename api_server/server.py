"""FastAPI application - CORS, session middleware, route registration."""

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from api_server.middleware.error_handler import (
    ErrorHandlerMiddleware,
    RequestIdMiddleware,
)
from api_server.middleware.rate_limit import RateLimitMiddleware
from api_server.routes import auth, health, services
from api_server.routes.payments import checkout, metering, subscription, webhooks
from common import global_config

app = FastAPI(title="mycli-api", version="0.1.0")

# --- Middleware (outermost first) -----------------------------------------

app.add_middleware(ErrorHandlerMiddleware)  # type: ignore[arg-type]
app.add_middleware(RateLimitMiddleware)  # type: ignore[arg-type]
app.add_middleware(RequestIdMiddleware)  # type: ignore[arg-type]

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


def main() -> None:
    """Entry-point for ``mycli-api`` console script."""
    uvicorn.run(
        "api_server.server:app",
        host=global_config.server.host,
        port=global_config.server.port,
        reload=False,
    )
