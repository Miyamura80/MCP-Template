"""Auto-register every service as an authenticated ``POST /api/v1/services/{name}``."""

from fastapi import APIRouter, Depends, Request

from api_server.auth import AuthenticatedUser
from api_server.auth.scopes import SERVICES_EXECUTE, require_scopes
from api_server.billing.limits import ensure_daily_limit
from api_server.idempotency import execute_idempotent
from services import ServiceEntry, discover_services, get_registry

router = APIRouter(prefix="/api/v1/services", tags=["services"])


def _register_service_routes() -> None:
    """Discover all service modules and create one route per service."""
    discover_services()
    for entry in get_registry():
        _make_route(entry)


def _make_route(entry: ServiceEntry) -> None:
    """Create a POST route, with Idempotency-Key support for mutating services."""
    if entry.mutating:
        _make_mutating_route(entry)
    else:
        _make_headless_route(entry)


def _make_headless_route(entry: ServiceEntry) -> None:
    """Create a POST route that mirrors the MCP tool pattern."""
    func = entry.func
    input_model = entry.input_model
    output_model = entry.output_model

    @router.post(
        f"/{entry.name}",
        response_model=output_model,
        summary=entry.description,
        name=f"svc_{entry.name}",
    )
    def _handler(
        body: input_model,  # ty: ignore[invalid-type-form]
        _user: AuthenticatedUser = Depends(require_scopes(SERVICES_EXECUTE)),
    ):
        if "user_id" in input_model.model_fields:  # ty: ignore[unresolved-attribute]
            body = body.model_copy(update={"user_id": _user.user_id})
        ensure_daily_limit(_user.user_id)
        return func(body)


def _make_mutating_route(entry: ServiceEntry) -> None:
    """Create a POST route that requires Idempotency-Key and replays retries."""
    func = entry.func
    input_model = entry.input_model
    output_model = entry.output_model

    @router.post(
        f"/{entry.name}",
        response_model=output_model,
        summary=entry.description,
        name=f"svc_{entry.name}",
    )
    def _handler(
        body: input_model,  # ty: ignore[invalid-type-form]
        request: Request,
        _user: AuthenticatedUser = Depends(require_scopes(SERVICES_EXECUTE)),
    ):
        if "user_id" in input_model.model_fields:  # ty: ignore[unresolved-attribute]
            body = body.model_copy(update={"user_id": _user.user_id})

        def _compute():
            # Run the quota check inside the claim so retries (replays) don't
            # double-count usage; the first execution still enforces the limit.
            ensure_daily_limit(_user.user_id)
            return func(body)

        return execute_idempotent(
            request=request,
            user_id=_user.user_id,
            route=entry.name,
            request_payload=body.model_dump(mode="json"),
            compute=_compute,
        )


_register_service_routes()
