"""Which tool exceptions the model is allowed to read.

python-sdk 2.x sanitizes tool failures: only a ``ToolError`` keeps its text;
any other exception reaches the client as a bare ``Error executing tool
<name>`` (``UnexpectedToolError``, original kept as ``__cause__``). v1 passed
every exception's text through.

This template's services fail with model-facing messages - a
``ConnectRequiredError`` carries its own recovery script, PDF/Gmail domain
errors say what to do next, a rejected argument says what to fix - and the
pure services must not import MCP types to say so. They opt in by raising a
:class:`services.ModelFacingError` (``InvalidArgument`` for a rejected
argument), whose text is first-party by contract. The server
(``mcp_server.server._TemplateMCPServer``) re-labels a crash caused by one of
those as a ``ToolError``, and keeps the SDK's sanitization for everything else.

The opt-in is by class, never by base type or module: a builtin such as
``ValueError`` or ``PermissionError`` is also what third-party code raises
(``requests.InvalidURL``, ``binascii.Error``, a pydantic ``ValidationError``
echoing ``input_value=`` of an upstream response, an ``OSError`` naming a
path), so passing those through would leak whatever text they carry. The
wrappers' own deliberate refusals (the scope check, input-model validation)
raise ``ToolError`` directly in ``mcp_server/_tool_factory.py``.

The one exception is the billing gate's ``HTTPException`` (daily quota 429,
misconfigured tier), which is HTTP-shaped because the REST transport shares it.
It passes only when raised from first-party billing code.
"""

from types import TracebackType

from starlette.exceptions import HTTPException

from services import ModelFacingError

# HTTPExceptions raised from these modules are written for the caller to read.
_FIRST_PARTY_HTTP_MODULES = ("api_server.billing",)


def _innermost_module(tb: TracebackType | None) -> str | None:
    """The module name of the frame that raised, or None without a traceback."""
    if tb is None:
        return None
    while tb.tb_next is not None:
        tb = tb.tb_next
    return tb.tb_frame.f_globals.get("__name__")


def _first_party_http_message(exc: HTTPException) -> str | None:
    module = _innermost_module(exc.__traceback__)
    if module is None or not any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in _FIRST_PARTY_HTTP_MODULES
    ):
        return None
    detail = exc.detail
    if isinstance(detail, dict):
        message = detail.get("message")
        return message if isinstance(message, str) else None
    return detail if isinstance(detail, str) else None


def model_facing_message(exc: BaseException | None) -> str | None:
    """Return the text the model may see for ``exc``, or None to stay sanitized."""
    if isinstance(exc, ModelFacingError):
        return str(exc)
    if isinstance(exc, HTTPException):
        return _first_party_http_message(exc)
    return None
