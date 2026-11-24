"""Service registry - pure business logic with no transport awareness."""

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, get_args

# How much harm a call can do, coarsest first. ``read`` has no side effects;
# ``write`` changes state the user can see and undo (a draft, a label);
# ``dangerous`` is hard or impossible to undo, or reaches other people (sending
# mail, deleting, revoking). Transports use it for MCP tool annotations, the
# ``?dangerous_tools=false`` filter, and (later) approval gating.
Risk = Literal["read", "write", "dangerous"]
RISKS: tuple[Risk, ...] = get_args(Risk)


@dataclass
class ServiceEntry:
    name: str
    description: str
    input_model: type
    output_model: type
    func: Callable[..., Any]
    mutating: bool = False
    risk: Risk = "read"
    # Sell-side (x402) pricing. ``price`` is the per-call amount as a positive
    # decimal string in units of ``asset`` (e.g. "0.001" USDC); ``None`` means
    # the service is free and stays on the daily-quota path. A priced service is
    # gated by the x402 paywall on the HTTP and MCP transports instead. Validated
    # at registration by :func:`service`.
    price: str | None = None
    asset: str = "USDC"


class ModelFacingError(Exception):
    """An error whose text is written for the model to read and act on.

    The MCP transport passes ``str(exc)`` of these (and only these) through to
    the client; every other exception reaches it as a bare ``Error executing
    tool <name>`` (see ``mcp_server/_tool_errors.py``). So the message must be
    first-party text: say what went wrong and what to do next, and never
    interpolate a third-party exception's text (an HTTP client, parser or
    database error may carry URLs, paths, DSNs or response bodies).
    """


class InvalidArgument(ModelFacingError, ValueError):  # noqa: N818 - name shared with the sibling MCP repos
    """A service rejected an argument; the caller's mistake to read and correct.

    Subclasses ``ValueError`` so existing ``except ValueError`` callers keep
    working. Raised before any side effect, so the REST transport maps it to
    400 and releases the ``Idempotency-Key`` claim for a clean retry.
    """


class ConnectRequiredError(ModelFacingError):
    """A service call requires the user to first complete an external connect flow.

    Transport-agnostic contract: each integration that links a third-party
    account (Gmail today; any future OAuth-backed service) raises its own
    subclass so transports can offer a recovery affordance without importing
    the feature - the MCP layer converts this into a SEP-1036 URL elicitation
    carrying ``build_auth_url()``. Two obligations on subclasses:

    - ``message`` must itself be a self-recovering script (what tool to call,
      what to do with the URL, then retry), because for hosts with no native
      affordance the exception text is the only channel that reaches them.
    - ``build_auth_url()`` returns the URL where the user completes the flow,
      or None when the flow is unconfigured in this deployment.
    """

    def __init__(self, user_id: str, message: str, *, elicitation_message: str) -> None:
        self.user_id = user_id
        self.elicitation_message = elicitation_message
        super().__init__(message)

    def build_auth_url(self) -> str | None:
        """Return the connect-flow URL for this user, or None if unconfigured."""
        raise NotImplementedError


_registry: list[ServiceEntry] = []
_discovered: bool = False


def _validate_price(name: str, price: str) -> None:
    """Reject a service registered with an empty or non-positive price.

    A malformed price would otherwise mark the service paid and let the paywall
    fall back to the x402 config's default amount, silently charging the wrong
    figure. Fail loudly at import time instead.
    """
    try:
        value = Decimal(price)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(
            f"@service({name!r}) price must be a decimal string, got {price!r}"
        ) from exc
    # Decimal accepts "NaN"/"Infinity" without raising; reject them before the
    # comparison below (NaN <= 0 itself raises InvalidOperation) so a priced
    # service can never advertise a non-finite x402 amount.
    if not value.is_finite():
        raise ValueError(
            f"@service({name!r}) price must be a finite amount, got {price!r}"
        )
    if value <= 0:
        raise ValueError(
            f"@service({name!r}) price must be a positive amount, got {price!r}"
        )


def _resolve_risk(name: str, mutating: bool, risk: Risk | None) -> tuple[bool, Risk]:
    """Return ``(mutating, risk)`` with the defaults and invariants applied.

    Unset ``risk`` follows ``mutating`` (``write`` or ``read``). ``dangerous``
    implies ``mutating``, since anything worth gating also needs idempotent
    retries. ``read`` with ``mutating=True`` is a contradiction and fails at
    import time rather than advertising a side effect as read-only.
    """
    if risk is None:
        return mutating, "write" if mutating else "read"
    if risk not in RISKS:
        raise ValueError(
            f"@service({name!r}) risk must be one of {RISKS}, got {risk!r}"
        )
    if risk == "read" and mutating:
        raise ValueError(f"@service({name!r}) is mutating, so risk cannot be 'read'")
    return mutating or risk == "dangerous", risk


def service(
    *,
    name: str,
    description: str,
    input_model: type,
    output_model: type,
    mutating: bool = False,
    risk: Risk | None = None,
    price: str | None = None,
    asset: str = "USDC",
):
    """Decorator that registers a function as a service.

    Set ``mutating=True`` for services with side effects (create/charge/send)
    so the HTTP transport enforces ``Idempotency-Key`` and replays the stored
    response on retries. Leave it ``False`` (the default) for pure/read-only
    services. On MCP, the flag also gates the enhancer crash-fallback
    (``mcp_server/_tool_factory.py``): a mutating service is never silently
    re-executed - the fallback reuses the already-completed ``tool.call()``
    result or propagates the enhancer error. CLI behavior is unchanged.

    ``risk`` ("read", "write" or "dangerous") defaults from ``mutating``. Mark
    a service ``dangerous`` when its effect is hard to undo or reaches other
    people (sending, deleting, revoking); that implies ``mutating``. On MCP it
    sets the tool annotations and lets a client hide the tool with
    ``/mcp?dangerous_tools=false``.

    Set ``price`` (a decimal string like "0.001", in units of ``asset``) to
    sell the tool via the x402 paywall: HTTP and MCP calls must carry a valid
    ``X-PAYMENT`` header, which the server verifies and settles before running
    the service. Priced services bypass the free daily quota. Leaving ``price``
    as ``None`` keeps the service free and quota-limited. The paywall only
    activates when x402 is enabled in config, so a priced service still runs
    free (quota-limited) on a deployment that hasn't turned x402 on - the
    template keeps working with no wallet configured. CLI is never gated.
    """
    if price is not None:
        _validate_price(name, price)
    mutating, resolved_risk = _resolve_risk(name, mutating, risk)
    if resolved_risk == "dangerous" and price is not None:
        # Approval runs the stored call later, outside the request that carried
        # the X-PAYMENT header, so a held call could not be charged correctly.
        raise ValueError(f"@service({name!r}) cannot be both dangerous and priced")

    def decorator(func):
        _registry.append(
            ServiceEntry(
                name=name,
                description=description,
                input_model=input_model,
                output_model=output_model,
                func=func,
                mutating=mutating,
                risk=resolved_risk,
                price=price,
                asset=asset,
            )
        )
        return func

    return decorator


def discover_services() -> None:
    """Import every ``services.*`` submodule so @service decorators run.

    Idempotent: safe to call from multiple transports during startup.
    """
    global _discovered
    if _discovered:
        return
    for module_info in pkgutil.iter_modules(__path__):
        importlib.import_module(f"services.{module_info.name}")  # noqa: TID251 - service auto-discovery so @service decorators register on startup
    _discovered = True


def get_registry() -> list[ServiceEntry]:
    """Return all registered services."""
    return list(_registry)


def dangerous_tool_names() -> frozenset[str]:
    """Names of ``risk="dangerous"`` services (the ones approval can hold)."""
    return frozenset(e.name for e in _registry if e.risk == "dangerous")
