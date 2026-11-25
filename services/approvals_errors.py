"""Errors the approval gate raises. Their text is written for the agent.

Nothing has run when one of these is raised, so transports treat them like a
rejected argument (the REST idempotency claim is released). Browser-mode holds
also carry what an MCP host needs to open the approval page as a URL
elicitation (``build_auth_url``, ``elicitation_message``).
"""

from __future__ import annotations

from models.approvals import ApprovalView
from services import ModelFacingError


class ApprovalGateError(ModelFacingError):
    """The gate stopped a call before it ran."""


class ApprovalBusyError(ApprovalGateError):
    """The same approved call is running right now."""


class ApprovalDeniedError(ApprovalGateError):
    """A human denied this exact call. Retrying it will not help."""


class ApprovalRequiredError(ApprovalGateError):
    """A dangerous call is held for approval."""

    def __init__(self, view: ApprovalView, call_hash: str = "") -> None:
        self.view = view
        # Identity of the exact held call; a host's answer must match it.
        self.call_hash = call_hash
        super().__init__(_instructions(view))


class BrowserApprovalRequiredError(ApprovalRequiredError):
    """Held for a human on the dashboard. Satisfies the URL-elicitation
    protocol in ``mcp_server/url_elicitation.py``."""

    def __init__(self, view: ApprovalView, call_hash: str, user_id: str) -> None:
        super().__init__(view, call_hash)
        self.user_id = user_id
        self.elicitation_message = (
            f"Approve {view.tool} before it runs. Open the page to approve or deny."
        )

    def build_auth_url(self) -> str | None:
        return self.view.approve_url


def required(view: ApprovalView, call_hash: str, user_id: str) -> ApprovalRequiredError:
    if view.mode == "browser":
        return BrowserApprovalRequiredError(view, call_hash, user_id)
    return ApprovalRequiredError(view, call_hash)


def _instructions(view: ApprovalView) -> str:
    expires = view.expires_at.isoformat(timespec="seconds")
    retry = "then retry this exact call to run it"
    if view.mode == "agent":
        return (
            f"{view.tool} needs confirmation before it runs. Re-read the arguments "
            f"you sent; if they are what the user wants, call "
            f"confirm_action(approval_id='{view.id}'), {retry}. Expires {expires}."
        )
    if view.mode == "elicitation":
        return (
            f"{view.tool} is waiting for the user to confirm in this app "
            f"(approval {view.id}, expires {expires}); {retry} once they do."
        )
    return (
        f"{view.tool} is waiting for the user to approve it at {view.approve_url} "
        f"(approval {view.id}, expires {expires}). Give the user that link. "
        f"Check with get_approval(approval_id='{view.id}'), {retry} once it says "
        "approved."
    )
