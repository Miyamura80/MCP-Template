"""Structured error responses and request-ID propagation."""

import uuid
from typing import Any

from fastapi import Request, Response
from loguru import logger as log
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

# Map HTTP status codes to error code strings
_STATUS_CODE_MAP: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    402: "payment_required",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "bad_gateway",
    503: "service_unavailable",
}


def _error_code(status: int) -> str:
    return _STATUS_CODE_MAP.get(status, "error")


def _is_stripe_error(exc: Exception) -> bool:
    """Check if an exception originates from Stripe."""
    return type(exc).__module__.startswith("stripe")


def _build_error_response(
    status_code: int,
    message: str,
    request_id: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {
            "code": _error_code(status_code),
            "message": message,
            "request_id": request_id,
        }
    }
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=body)


# ---------------------------------------------------------------------------
# Request-ID middleware
# ---------------------------------------------------------------------------


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Inject ``X-Request-ID`` from the incoming header or generate a uuid4."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# ---------------------------------------------------------------------------
# Error-handler middleware
# ---------------------------------------------------------------------------


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Catch exceptions and return a consistent JSON error envelope."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
        try:
            response = await call_next(request)
            # Convert non-2xx FastAPI/Starlette error responses into structured format
            if response.status_code >= 400:
                # Read the body to check if it's already structured
                body_bytes = b""
                async for chunk in response.body_iterator:  # type: ignore[union-attr]
                    body_bytes += chunk if isinstance(chunk, bytes) else chunk.encode()
                import json

                try:
                    data = json.loads(body_bytes)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    data = {}

                # If it already has our error envelope, just add the request ID
                if "error" in data and isinstance(data["error"], dict):
                    data["error"]["request_id"] = request_id
                    return JSONResponse(
                        status_code=response.status_code,
                        content=data,
                        headers=dict(response.headers),
                    )

                # Convert FastAPI's {"detail": "..."} format
                message = data.get("detail", "An error occurred")
                if isinstance(message, list):
                    # Pydantic validation errors
                    return _build_error_response(
                        response.status_code,
                        "Validation error",
                        request_id,
                        details={"errors": message},
                    )
                return _build_error_response(
                    response.status_code, str(message), request_id
                )

            return response

        except Exception as exc:
            log.exception("Unhandled exception in request {}", request_id)

            # Sanitize Stripe errors
            if _is_stripe_error(exc):
                return _build_error_response(
                    502,
                    "Payment processing error",
                    request_id,
                )

            return _build_error_response(
                500,
                "Internal server error",
                request_id,
            )
