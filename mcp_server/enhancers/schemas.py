"""Pydantic models for elicitation forms.

Per MCP spec, elicitation schemas must be flat objects with primitive fields
(str, int, float, bool, Literal[...] enums). The Python SDK accepts list[str]
but the spec doesn't - avoid for cross-client compatibility.
"""

from pydantic import BaseModel, Field


class ConfirmFix(BaseModel):
    """Confirm whether to auto-fix issues found by the doctor service."""

    fix: bool = Field(
        default=False,
        description="Auto-fix the fixable issues found?",
    )
