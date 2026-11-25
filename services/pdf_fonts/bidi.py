"""Bidi layout for one left-to-right overlay line (a subset of UAX #9).

Part of the PDF core (isolation seam): no Gmail imports.

Overlay text is a single line with a left-to-right base direction (stamps and
add_text are left-anchored) and never contains explicit embeddings: bidi
control characters are rejected up front (:data:`BIDI_CONTROLS`). That leaves
the weak/neutral rules (W1-W7, N1-N2), implicit levels (I1) and reordering
(L2), which fit in a page of code. A library was considered: python-bidi is
LGPL-3.0 (not permissive) and the pure-Python alternatives are unmaintained,
so the rules are implemented here and pinned by tests/test_pdf_bidi.py.

Levels: 0 = left-to-right, 1 = right-to-left, 2 = a number inside
right-to-left text (its digits stay left-to-right, the number stays inside
the reversed span).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

# Explicit directional formatting characters plus invisible zero-width ones
# that could disguise a name ("John" + RLO + "eod" displays as "John doe").
# ZWJ/ZWNJ (U+200D/U+200C) are kept: Arabic and Indic spelling needs them.
BIDI_CONTROLS = frozenset(
    {0x061C, 0x200B, 0x200E, 0x200F, 0xFEFF}
    | set(range(0x202A, 0x202F))
    | set(range(0x2066, 0x206A))
)

_STRONG = frozenset({"L", "R"})
_NEUTRAL = frozenset({"B", "S", "WS", "ON", "BN"})


def _weak_types(text: str) -> list[str]:
    """Bidi classes after rules W1-W7 (sos is L for a left-to-right line)."""
    types = [unicodedata.bidirectional(c) or "L" for c in text]
    previous = "L"
    for i, kind in enumerate(types):  # W1: marks take the preceding class
        if kind == "NSM":
            types[i] = previous
        previous = types[i]
    last_strong = "L"
    for i, kind in enumerate(types):  # W2: European digits after Arabic
        if kind in ("L", "R", "AL"):
            last_strong = kind
        elif kind == "EN" and last_strong == "AL":
            types[i] = "AN"
    types = ["R" if kind == "AL" else kind for kind in types]  # W3
    _join_number_separators(types)  # W4
    _attach_terminators(types)  # W5
    types = ["ON" if k in ("ES", "ET", "CS") else k for k in types]  # W6
    last_strong = "L"
    for i, kind in enumerate(types):  # W7: European digits after L
        if kind in _STRONG:
            last_strong = kind
        elif kind == "EN" and last_strong == "L":
            types[i] = "L"
    return types


def _join_number_separators(types: list[str]) -> None:
    """W4: a single separator between two digits of one kind ("12.5")."""
    for i in range(1, len(types) - 1):
        before, kind, after = types[i - 1], types[i], types[i + 1]
        if before == after and (
            (kind == "ES" and before == "EN")
            or (kind == "CS" and before in ("EN", "AN"))
        ):
            types[i] = before


def _attach_terminators(types: list[str]) -> None:
    """W5: a run of ET (e.g. '$', '%') next to a European number joins it."""
    i = 0
    while i < len(types):
        if types[i] != "ET":
            i += 1
            continue
        end = i
        while end < len(types) and types[end] == "ET":
            end += 1
        if (i > 0 and types[i - 1] == "EN") or (
            end < len(types) and types[end] == "EN"
        ):
            types[i:end] = ["EN"] * (end - i)
        i = end


def _strong_side(kind: str) -> str:
    # N1: numbers count as right-to-left when resolving neutrals.
    return "R" if kind in ("R", "EN", "AN") else "L"


def resolve_levels(text: str) -> list[int]:
    """Per-character embedding level for a left-to-right line."""
    types = _weak_types(text)
    i = 0
    while i < len(types):  # N1/N2: neutral runs between same-side strongs
        if types[i] not in _NEUTRAL:
            i += 1
            continue
        end = i
        while end < len(types) and types[end] in _NEUTRAL:
            end += 1
        before = _strong_side(types[i - 1]) if i > 0 else "L"
        after = _strong_side(types[end]) if end < len(types) else "L"
        types[i:end] = [before if before == after else "L"] * (end - i)
        i = end
    return [{"L": 0, "R": 1}.get(kind, 2) for kind in types]  # I1


def visual_order(levels: Sequence[int]) -> list[int]:
    """L2: indices of ``levels``' items in left-to-right drawing order."""
    order = list(range(len(levels)))
    for level in range(max(levels, default=0), 0, -1):
        i = 0
        while i < len(order):
            if levels[order[i]] < level:
                i += 1
                continue
            end = i
            while end < len(order) and levels[order[end]] >= level:
                end += 1
            order[i:end] = reversed(order[i:end])
            i = end
    return order
