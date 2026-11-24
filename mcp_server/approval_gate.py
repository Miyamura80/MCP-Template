"""MCP side of dangerous-tool approval (``services/approvals_svc.py``).

Two jobs the shared gate can't do, because they need the MCP request:

- Work out whether the host can ask its human right now
  (:func:`can_confirm_inline`). If it can't, the gate escalates an
  elicitation-mode call to browser.
- Run that conversation (:func:`confirm_inline`, :func:`resume_inline`). On
  2026-07-28 it is a multi-round-trip request: the tool call returns an
  ``InputRequiredResult`` asking "run this?", and the host retries the call
  with the human's answer and our ``request_state``. On handshake-era sessions
  with a back-channel it is an in-band ``elicitation/create``.

Either way the human's "yes" only flips the held call to ``approved``; the
retried call then goes through the gate, which runs exactly the stored call.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from anyio.to_thread import run_sync
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import (
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    InputRequiredResult,
)
from mcp.types.version import MODERN_PROTOCOL_VERSIONS
from pydantic import BaseModel

from mcp_server.enhancers.base import client_can_elicit
from mcp_server.url_elicitation import reraise_with_elicitation
from models.approvals import ApprovalView
from services import InvalidArgument, ServiceEntry
from services.approvals_svc import (
    BrowserApprovalRequiredError,
    GateDecision,
    approve_elicitation,
    check_gate,
    deny,
    get_action_args,
    record_outcome,
)
from src.utils.current_user import current_user
from src.utils.mcp_url_flags import current_url_flags

_STATE_PREFIX = "approval:"
_INPUT_KEY = "approval"
_ARGS_PREVIEW_CHARS = 600


class _Confirm(BaseModel):
    confirm: bool


def _modern_form_capable(ctx: Context | None) -> bool:
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
    # A bare ``elicitation: {}`` means form mode (spec back-compat rule).
    return elicitation is not None and (
        elicitation.form is not None or elicitation.url is None
    )


def _session(ctx: Context | None):
    if ctx is None:
        return None
    try:
        return ctx.session
    except (LookupError, ValueError, AttributeError):
        return None


def can_confirm_inline(ctx: Context | None) -> bool:
    """Whether this request can put a confirmation in front of the human."""
    return _modern_form_capable(ctx) or client_can_elicit(_session(ctx))


def gate(
    entry: ServiceEntry, input_obj: BaseModel, ctx: Context | None
) -> GateDecision:
    """Run the shared gate for an authenticated MCP call (sync; may hit the DB)."""
    user = current_user()
    try:
        return check_gate(
            entry,
            input_obj,
            user_id=user.user_id if user is not None else None,
            url_mode=current_url_flags().approval,
            can_confirm_inline=can_confirm_inline(ctx),
        )
    except BrowserApprovalRequiredError as exc:
        # Hosts with URL elicitation open the approval page themselves; the
        # error text carries the link for everyone else.
        reraise_with_elicitation(_session(ctx), exc)


async def gate_async(
    entry: ServiceEntry, input_obj: BaseModel, ctx: Context | None
) -> GateDecision:
    return await run_sync(gate, entry, input_obj, ctx)


def record(decision: GateDecision, result: BaseModel | None, error: str | None) -> None:
    if decision.action_id is not None:
        record_outcome(decision.action_id, result=result, error=error)


def _prompt(view: ApprovalView, args: dict[str, Any] | None) -> str:
    shown = {k: v for k, v in (args or {}).items() if k != "user_id"}
    preview = repr(shown)
    if len(preview) > _ARGS_PREVIEW_CHARS:
        preview = preview[: _ARGS_PREVIEW_CHARS - 1] + "…"
    return f"Allow the assistant to run {view.tool} with {preview}?"


async def confirm_inline(
    view: ApprovalView, ctx: Context, rerun
) -> InputRequiredResult | Any:
    """Ask the human about an elicitation-mode call held by the gate.

    ``rerun`` re-dispatches the original tool call once the answer is in.
    """
    user = current_user()
    if user is None:  # the gate never holds unauthenticated calls
        raise ToolError(str(view))
    args = await run_sync(get_action_args, user.user_id, view.id)
    message = _prompt(view, args)

    if _modern_form_capable(ctx):
        return InputRequiredResult(
            input_requests={
                _INPUT_KEY: ElicitRequest(
                    params=ElicitRequestFormParams(
                        message=message,
                        requested_schema=_Confirm.model_json_schema(),
                    )
                )
            },
            request_state=f"{_STATE_PREFIX}{view.id}",
        )

    answer = await ctx.elicit(message=message, schema=_Confirm)
    accepted = answer.action == "accept" and bool(
        getattr(answer.data, "confirm", False)
    )
    await _decide(user.user_id, view.id, view.tool, accepted)
    return await rerun()


async def resume_inline(name: str, ctx: Context | None) -> None:
    """Round two of a 2026-07-28 confirmation: apply the human's answer.

    Does nothing unless this retry carries our ``request_state``. The call
    that follows goes through the gate, which runs or refuses the held call.
    """
    if ctx is None:
        return
    try:
        state = ctx.request_state
        responses = ctx.input_responses
    except (LookupError, ValueError, AttributeError):
        return
    if not state or not state.startswith(_STATE_PREFIX):
        return
    user = current_user()
    if user is None:
        return
    answer = (responses or {}).get(_INPUT_KEY)
    accepted = (
        isinstance(answer, ElicitResult)
        and answer.action == "accept"
        and bool((answer.content or {}).get("confirm"))
    )
    await _decide(user.user_id, state.removeprefix(_STATE_PREFIX), name, accepted)


async def _decide(user_id: str, approval_id: str, tool: str, accepted: bool) -> None:
    if accepted:
        await run_sync(approve_elicitation, user_id, approval_id, tool)
    else:
        await run_sync(_deny_quietly, user_id, approval_id)


def _deny_quietly(user_id: str, approval_id: str) -> None:
    # A forged or foreign id has nothing of the caller's to deny.
    with suppress(InvalidArgument):
        deny(user_id, approval_id)
