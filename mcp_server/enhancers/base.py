"""EnhancedTool - typed wrapper passed to enhancer functions.

Provides capability detection, content emission helpers, and elicitation
passthrough while keeping the pure service callable via `tool.call()`.
"""

import os
from collections.abc import Callable

from mcp.server.elicitation import ElicitationResult
from mcp.server.fastmcp.server import Context
from mcp.types import (
    Annotations,
    AudioContent,
    CallToolResult,
    ClientCapabilities,
    ElicitationCapability,
    ImageContent,
    Role,
    TextContent,
)
from pydantic import BaseModel


def build_app_meta(resource_uri: str) -> dict:
    """Build the ``_meta`` dict declaring an MCP App resource.

    Used both on tool definitions (``tools/list``) and call results. Dual-keys
    the nested spec form and the deprecated flat form for legacy host compat.
    """
    return {
        "ui": {"resourceUri": resource_uri},
        "ui/resourceUri": resource_uri,
    }


def build_call_tool_result(
    model: BaseModel,
    *,
    app_uri: str | None = None,
    extra_content: list | None = None,
) -> CallToolResult:
    """Canonical CallToolResult assembly: text + structuredContent + app _meta.

    The single source of truth for how a service output model becomes the
    wire result the MCP spec expects. Both the enhanced-tool path
    (``_tool_factory._build_call_tool_result``) and the no-auth demo mount
    build results through here so their wire format cannot drift.
    """
    content: list = [
        TextContent(type="text", text=model.model_dump_json()),
        *(extra_content or []),
    ]
    kwargs: dict = {"content": content, "structuredContent": model.model_dump()}
    if app_uri is not None:
        kwargs["_meta"] = build_app_meta(app_uri)
    return CallToolResult(**kwargs)


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

    def call(self, override_input: TInput | None = None) -> TOutput:
        """Invoke the pure service. Pass `override_input` instead of mutating `tool.input`."""
        return self._service_fn(
            override_input if override_input is not None else self.input
        )

    @property
    def can_elicit(self) -> bool:
        return self._ctx.session.check_client_capability(
            ClientCapabilities(elicitation=ElicitationCapability())
        )

    @property
    def can_show_app(self) -> bool:
        """Best-effort check; spec lacks a standard capability for app rendering.

        Defaults to True. Set MCP_DISABLE_APPS=1 to force-skip on misbehaving clients.
        See mcp_server/MCP_UI_EDGE_CASES.md C2.
        """
        return os.environ.get("MCP_DISABLE_APPS", "") == ""

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
                type="image", data=data, mimeType=mime_type, annotations=annotations
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
                type="audio", data=data, mimeType=mime_type, annotations=annotations
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
