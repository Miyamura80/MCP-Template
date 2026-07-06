"""In-process state for the demo mount: draft cache + focus bridge.

The demo's mutations are simulated and stateless by design, with one
UX concession: a small bounded draft cache so the composer app's
save/refresh loop feels real within a browsing session. Best-effort only -
it evaporates on restart and is single-replica, matching the demo's
non-durability promise.
"""

from collections import OrderedDict
from typing import Any

from mcp_server.demo import fixtures
from models.gmail import GmailDraft, _UnsetType

_MAX_DRAFTS = 256
_drafts: OrderedDict[str, GmailDraft] = OrderedDict()

# Focus bridge (iframe UI <-> LLM), single demo persona - mirrors the
# in-memory dict the production gmail_inbox app tools use.
focused: dict[str, Any] | None = None


def remember_draft(draft: GmailDraft) -> GmailDraft:
    """Cache a draft (bounded LRU) and return it."""
    _drafts[draft.draft_id] = draft
    _drafts.move_to_end(draft.draft_id)
    while len(_drafts) > _MAX_DRAFTS:
        _drafts.popitem(last=False)
    return draft


def get_draft(draft_id: str) -> GmailDraft:
    """Fetch a cached draft; the seed draft self-heals after eviction/restart."""
    cached = _drafts.get(draft_id)
    if cached is not None:
        return cached
    if draft_id == fixtures.SEED_DRAFT.draft_id:
        return remember_draft(fixtures.SEED_DRAFT.model_copy(deep=True))
    raise ValueError(
        f"Unknown demo draft {draft_id!r}. Create one with gmail_compose or "
        "gmail_reply_to_thread first (demo drafts do not survive a server "
        "restart)."
    )


def drop_draft(draft_id: str) -> None:
    _drafts.pop(draft_id, None)


def patch_field(current: str | None, value: str | None | _UnsetType) -> str | None:
    """UNSET preserves the current value; None clears it; a string replaces it."""
    if isinstance(value, _UnsetType):
        return current
    return value


def set_focused(data: dict[str, Any] | None) -> None:
    global focused
    focused = data
