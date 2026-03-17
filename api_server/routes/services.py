"""Auto-register every service as an authenticated ``POST /api/v1/services/{name}``."""

from fastapi import APIRouter, Depends

from api_server.auth import AuthenticatedUser
from api_server.auth.scopes import SERVICES_EXECUTE, require_scopes
from api_server.billing.limits import ensure_daily_limit
from services import ServiceEntry

router = APIRouter(prefix="/api/v1/services", tags=["services"])


def _register_service_routes() -> None:
    """Discover all service modules and create one route per service."""
    import importlib
    import pkgutil

    import services as _services_pkg

    for module_info in pkgutil.iter_modules(_services_pkg.__path__):
        importlib.import_module(f"services.{module_info.name}")

    from services import get_registry

    for entry in get_registry():
        _make_route(entry)


def _make_route(entry: ServiceEntry) -> None:
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
        body: input_model,  # type: ignore[valid-type]
        _user: AuthenticatedUser = Depends(require_scopes(SERVICES_EXECUTE)),
    ):
        # Quota is consumed before execution (charges for attempts, not
        # results) to prevent abuse via intentional error-triggering.
        ensure_daily_limit(_user.user_id)
        return func(body)


_register_service_routes()
