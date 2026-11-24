"""EnhancedTool - typed wrapper passed to enhancer functions.

Provides capability detection, content emission helpers, and elicitation
passthrough while keeping the pure service callable via `tool.call()`.

Two ways to ask the user something mid-call, by protocol era:

- handshake-era sessions with a back-channel: ``tool.elicit()`` sends an
  in-band ``elicitation/create`` (``tool.can_elicit``);
- 2026-07-28 (stateless): the enhancer returns ``tool.input_required(...)``,
  an ``InputRequiredResult``; the host asks the user and retries the same
  call with ``inputResponses`` and our ``requestState``, which the retry reads
  with ``tool.input_response()`` / ``tool.request_state``
  (``tool.can_request_input``). The SDK seals ``requestState`` and binds it to
  the user and the call (``mcp_server.server._request_state_security``).
"""

import os
from collections.abc import Callable

from mcp.server.elicitation import ElicitationResult
from mcp.server.mcpserver import Context
from mcp.types import (
    Annotations,
    AudioContent,
    ClientCapabilities,
    ElicitationCapability,
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    ImageContent,
    InputRequiredResult,
    Role,
    TextContent,
)
from mcp.types.version import MODERN_PROTOCOL_VERSIONS
from pydantic import BaseModel


def client_can_elicit(session) -> bool:
    """True only when an in-band ``elicitation/create`` can actually complete.

    Two conditions, both required:

    - the client DECLARED the elicitation capability (in ``initialize`` on
      handshake-era revisions, in each request's ``_meta`` on 2026-07-28), and
    - this request has a back-channel for server-initiated requests
      (``session.can_send_request``). That is False on every 2026-07-28
      request (the protocol removed server-initiated requests; input there is
      gathered via multi-round-trip ``InputRequiredResult``), on stateless
      legacy HTTP (the client's reply POST is uncorrelatable) and in
      JSON-response mode. ``ctx.elicit()`` would raise ``NoBackChannelError``
      (or, in v1, hang) in those cases.

    Deliberately conservative: never attempt-when-unknown.
    """
    if session is None:
        return False
    if not session.can_send_request:
        return False
    return session.check_client_capability(
        ClientCapabilities(elicitation=ElicitationCapability())
    )


def client_can_answer_form(ctx: Context | None) -> bool:
    """True when this 2026-07-28 request can answer a form ``InputRequiredResult``.

    Requires a modern protocol version and a declared form-mode elicitation
    capability. A bare ``elicitation: {}`` means form mode (spec back-compat
    rule).
    """
    if ctx is None:
        return False
    try:
        version = ctx.protocol_version
        caps = ctx.client_capabilities
    except (LookupError, ValueError, AttributeError):
        return False
    if version not in MODERN_PROTOCOL_VERSIONS or caps is None:
        return False
    elicitation = caps.elicitation
    return elicitation is not None and (
        elicitation.form is not None or elicitation.url is None
    )


def build_app_meta(resource_uri: str) -> dict:
    """Build the ``_meta`` dict declaring an MCP App resource.

    Used both on tool definitions (``tools/list``) and call results. Dual-keys
    the nested spec form and the deprecated flat form for legacy host compat.
    """
    return {
        "ui": {"resourceUri": resource_uri},
        "ui/resourceUri": resource_uri,
    }


class EnhancedTool[TInput: BaseModel, TOutput: BaseModel]:
    """Wrapper around a pure service, passed to enhancer functions.

    Enhancer code uses this to call the pure service, check client capabilities,
    elicit user input, and attach extra content (images, audio, text, app meta)
    to the response.
    """

    def __init__(
        self,
        ctx: Context,
        input: TInput,
        service_fn: Callable[[TInput], TOutput],
    ):
        self._ctx = ctx
        self.input = input
        self._service_fn = service_fn
        self.extra_content: list[TextContent | ImageContent | AudioContent] = []
        self.app_resource_uri: str | None = None
        # Result of the most recent *completed* `call()`, or None if the last
        # `call()` raised (or never ran). Lets the fallback path in
        # `mcp_server._tool_factory` reuse the result instead of re-executing
        # the service (which would duplicate side effects for mutating
        # services) when the enhancer crashes after `call()` completed.
        self.call_result: TOutput | None = None

    def call(self, override_input: TInput | None = None) -> TOutput:
        """Invoke the pure service. Pass `override_input` instead of mutating `tool.input`."""
        # Reset first: a stale stash from an earlier call must not mask a
        # failure of *this* invocation as success in the crash-fallback path.
        self.call_result = None
        result = self._service_fn(
            override_input if override_input is not None else self.input
        )
        self.call_result = result
        return result

    @property
    def can_elicit(self) -> bool:
        """True only when the client declared elicitation AND it can complete.

        See :func:`client_can_elicit`. Under 2026-07-28 and under
        ``stateless_http=True`` (the production default for legacy clients)
        this is False even for elicitation-capable hosts, so enhancers take
        their non-elicitation fallbacks. Do NOT change this to
        attempt-when-unknown. See the transport comment in
        ``mcp_server/server.py``.
        """
        return client_can_elicit(self._ctx.session)

    @property
    def can_show_app(self) -> bool:
        """Best-effort check; spec lacks a standard capability for app rendering.

        Defaults to True. Set MCP_DISABLE_APPS=1 to force-skip on misbehaving clients.
        See mcp_server/MCP_UI_EDGE_CASES.md C2.
        """
        return os.environ.get("MCP_DISABLE_APPS", "") == ""

    @property
    def can_request_input(self) -> bool:
        """True when a 2026-07-28 form round trip can ask the user (see module doc)."""
        return client_can_answer_form(self._ctx)

    @property
    def request_state(self) -> str | None:
        """The plaintext ``requestState`` this retry echoes, if any (SDK-verified)."""
        try:
            return self._ctx.request_state
        except (LookupError, ValueError, AttributeError):
            return None

    def input_response(self, key: str) -> ElicitResult | None:
        """The user's answer to the input request named ``key`` on this retry."""
        try:
            responses = self._ctx.input_responses or {}
        except (LookupError, ValueError, AttributeError):
            return None
        answer = responses.get(key)
        return answer if isinstance(answer, ElicitResult) else None

    def input_required(
        self, key: str, message: str, schema: type[BaseModel], request_state: str
    ) -> InputRequiredResult:
        """Ask for a form (one flat primitive-only model) and end this round.

        Return it from the enhancer; the host retries the call with the answer.
        """
        return InputRequiredResult(
            input_requests={
                key: ElicitRequest(
                    params=ElicitRequestFormParams(
                        message=message, requested_schema=schema.model_json_schema()
                    )
                )
            },
            request_state=request_state,
        )

    async def elicit(self, message: str, schema: type[BaseModel]) -> ElicitationResult:
        """Ask the user for input mid-tool. Schema must be a flat primitive-only BaseModel."""
        return await self._ctx.elicit(message=message, schema=schema)

    def send_text(
        self,
        text: str,
        audience: list[Role] | None = None,
    ) -> None:
        annotations = _annotations(audience)
        self.extra_content.append(
            TextContent(type="text", text=text, annotations=annotations)
        )

    def send_image(
        self,
        data: str,
        mime_type: str,
        audience: list[Role] | None = None,
    ) -> None:
        """Append a base64-encoded image to the response."""
        annotations = _annotations(audience)
        self.extra_content.append(
            ImageContent(
                type="image", data=data, mime_type=mime_type, annotations=annotations
            )
        )

    def send_audio(
        self,
        data: str,
        mime_type: str,
        audience: list[Role] | None = None,
    ) -> None:
        """Append a base64-encoded audio clip to the response."""
        annotations = _annotations(audience)
        self.extra_content.append(
            AudioContent(
                type="audio", data=data, mime_type=mime_type, annotations=annotations
            )
        )

    def send_app(self, resource_uri: str) -> None:
        """Attach an MCP App resource to the response. Caller-resolved `ui://` URI."""
        self.app_resource_uri = resource_uri

    def app_meta(self) -> dict | None:
        """Build the _meta dict for app attachment, or None if no app was sent."""
        if self.app_resource_uri is None:
            return None
        return build_app_meta(self.app_resource_uri)


def _annotations(audience: list[Role] | None):
    if audience is None:
        return None
    return Annotations(audience=audience)
