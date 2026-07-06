"""Stateless helpers for the demo mount - deliberately no mutable state.

``/mcp-demo`` is public and unauthenticated, and the transport runs in
``stateless_http`` mode, so there is NO per-visitor key available server-side
(no auth principal, no session id). Any server-side mutable store would
therefore be shared across every anonymous visitor - a cross-visitor prompt-
injection channel (via the focus bridge) and shared draft edits. So the demo
keeps nothing: mutations echo their inputs, drafts are reconstructed
deterministically, and the focus bridge is a no-op. Everything here is a pure
function.
"""

from uuid import uuid4

from mcp_server.demo import fixtures
from models.gmail import GmailDraft, _UnsetType


def new_draft_id() -> str:
    """A fresh demo draft id (prefixed so it's obviously not a real Gmail id)."""
    return f"demo-d-{uuid4().hex[:8]}"


def reply_subject(explicit: str | None, original: str | None) -> str:
    """Derive a reply subject: an explicit value wins; else ``Re:``-prefix the
    original unless it already carries one. Single source of truth for the rule
    (the inbox-app reply and the LLM reply tool both call it)."""
    subject = explicit or original or ""
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def patch_field(current: str | None, value: str | None | _UnsetType) -> str | None:
    """UNSET preserves the current value; None clears it; a string replaces it."""
    if isinstance(value, _UnsetType):
        return current
    return value


def echo_saved_draft(draft_id: str, **fields: str | None | _UnsetType) -> GmailDraft:
    """Reconstruct a "saved" draft statelessly.

    With no per-visitor store the base is deterministic: the seed draft for its
    known id, otherwise an empty draft carrying that id. Provided fields are
    merged with patch semantics (UNSET preserves, None clears). This is a
    demo - it never has to reflect another call's edits, only echo this one's.
    """
    if draft_id == fixtures.SEED_DRAFT.draft_id:
        base = fixtures.SEED_DRAFT.model_copy(deep=True)
    else:
        base = GmailDraft(draft_id=draft_id)
    return base.model_copy(
        update={
            key: patch_field(getattr(base, key), value) for key, value in fields.items()
        }
    )
