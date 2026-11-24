"""Auto-register every service as an authenticated ``POST /api/v1/services/{name}``."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api_server.auth import AuthenticatedUser
from api_server.auth.scopes import SERVICES_EXECUTE, require_scopes
from api_server.billing.limits import ensure_daily_limit
from api_server.billing.paywall import enforce_payment
from api_server.idempotency import execute_idempotent
from services import ServiceEntry, discover_services, get_registry
from services.approvals_svc import (
    ApprovalDeniedError,
    ApprovalGateError,
    ApprovalRequiredError,
    GateDecision,
    check_gate,
    record_outcome,
)

router = APIRouter(prefix="/api/v1/services", tags=["services"])


def _register_service_routes() -> None:
    """Discover all service modules and create one route per service."""
    discover_services()
    for entry in get_registry():
        _make_route(entry)


def _make_route(entry: ServiceEntry) -> None:
    """Register ``POST /api/v1/services/{name}`` for one service.

    Read-only services run the compute directly. Mutating services run the same
    compute through ``execute_idempotent``, which enforces ``Idempotency-Key``
    and replays the stored response on retries.
    """
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
            # Dangerous services may be held for approval (raises; nothing has
            # run). REST can't ask the human inline, so elicitation-mode
            # settings escalate to browser. A retry of an approved call gets
            # its stored result.
            decision = check_gate(
                entry, body, user_id=_user.user_id, url_mode=None,
                can_confirm_inline=False,
            )  # fmt: skip
            if decision.replay is not None:
                return output_model.model_validate(decision.replay)
            # Priced services are gated by the x402 paywall (verify + settle the
            # X-PAYMENT header). When the paywall actually charges the call it
            # bypasses the free quota; when it's inactive (x402 disabled) it
            # returns False and we still apply the daily quota, so a priced
            # service is never unlimited. Both run inside the compute so
            # idempotent replays don't double-charge or double-count.
            charged = False
            price = entry.price
            if price is not None:
                charged = enforce_payment(
                    user_id=_user.user_id,
                    route=entry.name,
                    price=price,
                    asset=entry.asset,
                    payment_header=request.headers.get("X-PAYMENT"),
                    mutating=entry.mutating,
                )
            if not charged:
                ensure_daily_limit(_user.user_id)
            return _run_recorded(decision, lambda: func(body))

        try:
            if not entry.mutating:
                return _compute()
            return execute_idempotent(
                request=request,
                user_id=_user.user_id,
                route=entry.name,
                request_payload=body.model_dump(mode="json"),
                compute=_compute,
            )
        except ApprovalGateError as exc:
            return _gate_response(exc)


def _run_recorded(decision: GateDecision, run):
    """Run the service; for an approved held call, store how it ended."""
    if decision.action_id is None:
        return run()
    try:
        result = run()
    except Exception as exc:  # noqa: BLE001
        # Approval bookkeeping only: an approved call must not stay
        # "executing" forever. Re-raised unchanged for the normal handlers.
        record_outcome(decision.action_id, error=f"failed: {type(exc).__name__}")
        raise
    record_outcome(decision.action_id, result=result)
    return result


def _gate_response(exc: ApprovalGateError) -> JSONResponse:
    """Nothing ran. Held: ``202 Accepted`` (retry the same call after approval
    to get its stored result, or poll ``get_approval``). Denied: 403. The same
    approved call already running: 409."""
    if isinstance(exc, ApprovalDeniedError):
        return JSONResponse(status_code=403, content={"detail": str(exc)})
    if not isinstance(exc, ApprovalRequiredError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    return JSONResponse(
        status_code=202,
        content={
            "status": "approval_required",
            "message": str(exc),
            "approval": exc.view.model_dump(mode="json"),
        },
    )


_register_service_routes()
