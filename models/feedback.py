"""Pydantic schemas for the agent ``feedback`` service.

An agent that gets stuck (confusing docs, a missing tool, an error it cannot
recover from) reports it here instead of silently giving up. ``user_id`` is
injected by the transport from the authenticated principal, like the Gmail
models, so the operator can follow up with the human behind the agent.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

FeedbackCategory = Literal["bug", "confusing_docs", "missing_tool", "other"]


class FeedbackInput(BaseModel):
    user_id: str = Field(default="", description="Submitting user (injected)")
    message: str = Field(
        description=(
            "What you were trying to do, what happened, and what you expected. "
            "Do not include secrets or the user's private data."
        ),
        min_length=1,
        max_length=4000,
    )
    category: FeedbackCategory = Field(
        default="other",
        description=(
            "bug: a tool misbehaved; confusing_docs: a description or doc misled "
            "you; missing_tool: you needed a capability that does not exist"
        ),
    )
    tool_name: str | None = Field(
        default=None,
        description="The tool the feedback is about, if any",
        max_length=128,
    )
    context: str | None = Field(
        default=None,
        description="Optional extra detail, e.g. the arguments or error text",
        max_length=4000,
    )


class FeedbackResult(BaseModel):
    id: str
    received_at: datetime
    forwarded_to: list[str] = Field(
        default_factory=list,
        description="Sinks (e.g. 'slack') that accepted the feedback",
    )
