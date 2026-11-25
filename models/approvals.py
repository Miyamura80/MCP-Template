"""Contracts for human-in-the-loop approval of dangerous tool calls."""

from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

# Loosest to strictest. Order matters: the effective mode for a call is the
# stricter of the user's dashboard setting and the MCP URL's ``?approval=``.
ApprovalMode = Literal["automatic", "agent", "elicitation", "browser"]
APPROVAL_MODES: tuple[ApprovalMode, ...] = get_args(ApprovalMode)

# pending  -> waiting for a decision
# approved -> a human accepted an elicitation; the retried call runs it next
# executing, executed, failed -> the stored call ran (or is running)
# denied, expired -> it never will
ApprovalStatus = Literal[
    "pending", "approved", "executing", "executed", "failed", "denied", "expired"
]


def stricter(a: ApprovalMode, b: ApprovalMode | None) -> ApprovalMode:
    """The stricter of two modes; ``None`` means "no preference"."""
    if b is None:
        return a
    return max(a, b, key=APPROVAL_MODES.index)


class ApprovalView(BaseModel):
    """One pending (or resolved) dangerous call, as an agent or dashboard sees it."""

    id: str
    tool: str
    mode: ApprovalMode
    status: ApprovalStatus
    created_at: datetime
    expires_at: datetime
    approve_url: str | None = Field(
        default=None, description="Page where a human approves (browser mode)."
    )
    result: dict[str, Any] | None = Field(
        default=None, description="The tool's output, once executed."
    )
    error: str | None = None


class ApprovalIdInput(BaseModel):
    user_id: str = ""
    approval_id: str = Field(min_length=1, max_length=64)


class ApprovalSettings(BaseModel):
    """A user's approval floor: one mode for every dangerous tool, plus overrides."""

    mode: ApprovalMode = "automatic"
    overrides: dict[str, ApprovalMode] = Field(
        default_factory=dict, description="Per-tool mode, keyed by tool name."
    )


class ApprovalList(BaseModel):
    approvals: list[ApprovalView]
