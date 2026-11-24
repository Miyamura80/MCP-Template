"""MCP side of dangerous-tool approval (``services/approvals_svc.py``).

Two jobs the shared gate can't do, because they need the MCP request:

- Work out whether the host can ask its human right now
  (:func:`can_confirm_inline`). If it can't, the gate escalates an
  elicitation-mode call to browser.
- Run that conversation (:func:`confirm_inline`, :func:`resume_inline`). On
  2026-07-28 it is a multi-round-trip request: the tool call returns an
  ``InputRequiredResult`` asking "run this?", and the host retries the call
  with the human's answer and our signed ``request_state``. On handshake-era
  sessions with a back-channel it is an in-band ``elicitation/create``.

Either way the human's "yes" only flips the held call to ``approved``, bound to
that exact call; the retried call then goes through the gate, which runs it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from anyio.to_thread import run_sync
from itsdangerous import BadSignature, URLSafeTimedSerializer
from mcp.server.mcpserver import Context
from mcp.types import (
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    InputRequiredResult,
)
from pydantic import BaseModel

from common import global_config
from mcp_server.enhancers.base import client_can_answer_form, client_can_elicit
from mcp_server.url_elicitation import reraise_with_elicitation
from services import InvalidArgument, ServiceEntry
from services.approvals_errors import (
    ApprovalRequiredError,
    BrowserApprovalRequiredError,
)
from services.approvals_svc import (
    GateDecision,
    approve_elicitation,
    check_gate,
    deny,
    get_review,
)
from src.utils.current_user import current_user
from src.utils.mcp_url_flags import current_url_flags

_STATE_PREFIX = "approval:"
_STATE_SALT = "mymcp.approvals.request-state"
_INPUT_KEY = "approval"
_ARGS_PREVIEW_CHARS = 600


class _Confirm(BaseModel):
    confirm: bool


def _state_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        global_config.SESSION_SECRET_KEY,
        salt=_STATE_SALT,
        signer_kwargs={"digest_method": hashlib.sha256},
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
    return client_can_answer_form(ctx) or client_can_elicit(_session(ctx))


def gate(
    entry: ServiceEntry, input_obj: BaseModel, ctx: Context | None
) -> GateDecision:
    """Run the shared gate for an MCP call (sync; may hit the DB).

    No bound user means stdio or in-process: the local operator, never gated.
    /mcp over HTTP always authenticates first (``MCPAuthMiddleware``).
    """
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


def _prompt(tool: str, args: dict[str, Any]) -> str:
    preview = repr(args)
    if len(preview) > _ARGS_PREVIEW_CHARS:
        preview = preview[: _ARGS_PREVIEW_CHARS - 1] + "…"
    return f"Allow the assistant to run {tool} with {preview}?"


async def confirm_inline(
    held: ApprovalRequiredError,
    ctx: Context,
    rerun: Callable[[], Awaitable[Any]],
) -> InputRequiredResult | Any:
    """Ask the human about an elicitation-mode call the gate just held.

    ``rerun`` re-dispatches the original tool call once the answer is in.
    """
    user = current_user()
    view = held.view
    if user is None:  # the gate never holds unauthenticated calls
        raise held
    _, args = await run_sync(get_review, user.user_id, view.id)
    message = _prompt(view.tool, args)

    if client_can_answer_form(ctx):
        state = _state_serializer().dumps([view.id, held.call_hash])
        return InputRequiredResult(
            input_requests={
                _INPUT_KEY: ElicitRequest(
                    params=ElicitRequestFormParams(
                        message=message,
                        requested_schema=_Confirm.model_json_schema(),
                    )
                )
            },
            request_state=f"{_STATE_PREFIX}{state}",
        )

    answer = await ctx.elicit(message=message, schema=_Confirm)
    accepted = answer.action == "accept" and bool(
        getattr(answer.data, "confirm", False)
    )
    await _decide(user.user_id, view.id, view.tool, held.call_hash, accepted)
    return await rerun()


async def resume_inline(name: str, ctx: Context | None) -> None:
    """Round two of a 2026-07-28 confirmation: apply the human's answer.

    Does nothing unless this retry carries our signed ``request_state``. The
    call that follows goes through the gate, which runs or refuses it.
    """
    if ctx is None:
        return
    try:
        state = ctx.request_state
        responses = ctx.input_responses
    except (LookupError, ValueError, AttributeError):
        return
    user = current_user()
    if not state or not state.startswith(_STATE_PREFIX) or user is None:
        return
    try:
        approval_id, call_hash = _state_serializer().loads(
            state.removeprefix(_STATE_PREFIX),
            max_age=global_config.approvals.elicitation_expiry_seconds,
        )
    except (BadSignature, ValueError, TypeError):
        return  # not ours, tampered with, or stale: the gate re-asks
    answer = (responses or {}).get(_INPUT_KEY)
    accepted = (
        isinstance(answer, ElicitResult)
        and answer.action == "accept"
        and bool((answer.content or {}).get("confirm"))
    )
    await _decide(user.user_id, str(approval_id), name, str(call_hash), accepted)


async def _decide(
    user_id: str, approval_id: str, tool: str, call_hash: str, accepted: bool
) -> None:
    if accepted:
        await run_sync(approve_elicitation, user_id, approval_id, tool, call_hash)
    else:
        await run_sync(_deny_quietly, user_id, approval_id, tool)


def _deny_quietly(user_id: str, approval_id: str, tool: str) -> None:
    # A foreign id, or one for another tool, has nothing of the caller's to deny.
    with suppress(InvalidArgument):
        deny(user_id, approval_id, tool=tool)
