"""Per-connection options carried as query parameters on the MCP URL.

A user configures an agent with e.g. ``https://host/mcp?dangerous_tools=false``
and every request on that connection carries the flags. The /mcp middleware
parses them off the ASGI scope and binds them here, the same way the
authenticated user is bound in ``src/utils/current_user.py``. Stdio and
in-process callers see the defaults.

The flags only ever narrow what a connection can do. They are a convenience,
not a security control: whoever holds the token can also edit the URL.

- ``dangerous_tools`` (default ``true``): ``false`` hides ``risk="dangerous"``
  tools from ``tools/list`` and refuses calls to them.
- ``approval``: a requested approval mode. Parsed and validated now; it takes
  effect once approval gating lands, and can only tighten the dashboard
  setting, never loosen it.

Unknown parameters are ignored so a client's own query parameters (or future
flags on an older server) don't break the connection.
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Literal, get_args
from urllib.parse import parse_qs

# Loosest to strictest. Order matters: the effective mode is the stricter of
# the dashboard setting and the URL flag.
ApprovalMode = Literal["automatic", "agent", "elicitation", "browser"]
APPROVAL_MODES: tuple[ApprovalMode, ...] = get_args(ApprovalMode)

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})


class InvalidUrlFlagError(ValueError):
    """A recognised flag had a value we can't interpret. Text is client-facing."""


@dataclass(frozen=True)
class McpUrlFlags:
    dangerous_tools: bool = True
    approval: ApprovalMode | None = None


DEFAULT_FLAGS = McpUrlFlags()

_flags: ContextVar[McpUrlFlags] = ContextVar("mcp_url_flags", default=DEFAULT_FLAGS)


def _single(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    if not values:
        return None
    if len(set(values)) > 1:
        raise InvalidUrlFlagError(f"MCP URL flag {key!r} is given more than once.")
    return values[0].strip().lower()


def parse_url_flags(query_string: bytes | str) -> McpUrlFlags:
    """Parse the MCP URL's query string. Raises ``InvalidUrlFlagError``."""
    if isinstance(query_string, bytes):
        query_string = query_string.decode("latin-1")
    params = parse_qs(query_string, keep_blank_values=True)

    dangerous_tools = True
    raw = _single(params, "dangerous_tools")
    if raw is not None:
        if raw in _TRUE:
            dangerous_tools = True
        elif raw in _FALSE:
            dangerous_tools = False
        else:
            raise InvalidUrlFlagError(
                f"MCP URL flag dangerous_tools={raw!r} is not a boolean. "
                "Use dangerous_tools=true or dangerous_tools=false."
            )

    approval = _single(params, "approval")
    if approval is not None and approval not in APPROVAL_MODES:
        raise InvalidUrlFlagError(
            f"MCP URL flag approval={approval!r} is not a mode. "
            f"Use one of: {', '.join(APPROVAL_MODES)}."
        )

    return McpUrlFlags(dangerous_tools=dangerous_tools, approval=approval)  # ty: ignore[invalid-argument-type]


def current_url_flags() -> McpUrlFlags:
    """Return the flags for this request (defaults outside /mcp)."""
    return _flags.get()


def set_url_flags(flags: McpUrlFlags) -> "Token[McpUrlFlags]":
    return _flags.set(flags)


def reset_url_flags(token: "Token[McpUrlFlags]") -> None:
    _flags.reset(token)
