"""Unicode font chain for PDF overlay text and the signature stamp.

Part of the PDF core (isolation seam): no Gmail imports.

Text outside Latin-1 cannot be drawn with the standard-14 faces, so
:mod:`services.pdf_overlay` embeds a subset of a real font for it. This
module owns *which* font: an ordered chain of bundled Noto subsets (see
``OFL.txt`` and ``scripts/build_pdf_fonts.py`` for provenance and coverage)
followed by any operator-supplied ``pdf_forms.fonts.extra_font_paths``. Each
character goes to the first face whose cmap has it; text is then split into
runs of one face and one direction so HarfBuzz can shape each run (Arabic
joining, right-to-left glyph order) before embedding.

The bundled subsets are a size trade-off: full Noto CJK is ~16 MB per face,
these total ~2.8 MB and cover kana, JIS X 0208 level-1 kanji, GB2312
level-1 hanzi and the 2,350 common Hangul syllables. Rarer ideographs and
unbundled scripts are reported by :func:`uncovered_chars` so callers refuse
them up front instead of stamping a ``.notdef`` box.
"""

from __future__ import annotations

import functools
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from fontTools.ttLib import TTFont, TTLibError
from loguru import logger as log

from common import global_config
from common.global_config import root_dir

FONT_DIR = Path(__file__).resolve().parent

# Chain order matters: the first face covering a character wins. Latin goes
# first so mixed lines ("Signed by ... on <date>") keep one Latin face; JP
# precedes SC so shared ideographs get Japanese glyph shapes.
BUNDLED_FONTS = (
    "NotoSans-Regular.subset.ttf",
    "NotoSansArabic-Regular.subset.ttf",
    "NotoSansJP-Regular.subset.ttf",
    "NotoSansSC-Regular.subset.ttf",
    "NotoSansKR-Regular.subset.ttf",
)

_RTL_CLASSES = frozenset({"R", "AL", "AN"})
# Bidi-neutral characters (spaces, punctuation, combining marks) join the
# current run when its face has them, so "محمد علي" shapes as one RTL run
# instead of being split at the space.
_NEUTRAL_CLASSES = frozenset({"WS", "ON", "CS", "ES", "ET", "BN", "NSM", "B", "S"})


@dataclass(frozen=True)
class FontFace:
    """One font file in the chain: its bytes (for embedding) and cmap."""

    path: Path
    data: bytes
    cmap: frozenset[int]


@dataclass(frozen=True)
class TextRun:
    """A maximal slice of text drawn with one face in one direction."""

    face: FontFace
    text: str
    rtl: bool


def _load_face(path: Path) -> FontFace:
    data = path.read_bytes()
    cmap = TTFont(path, lazy=True).getBestCmap() or {}
    return FontFace(path=path, data=data, cmap=frozenset(cmap))


@functools.cache
def _load_chain(
    bundled: tuple[str, ...], extra_paths: tuple[str, ...]
) -> tuple[FontFace, ...]:
    faces: list[FontFace] = []
    for name in bundled:
        try:
            faces.append(_load_face(FONT_DIR / name))
        except (OSError, TTLibError) as exc:
            # A broken install (fonts stripped from the wheel) must be loud:
            # every non-Latin-1 name would be refused without an obvious cause.
            log.error("Bundled PDF font {} failed to load: {}", name, exc)
    for raw in extra_paths:
        path = Path(raw)
        if not path.is_absolute():
            path = root_dir / path
        try:
            faces.append(_load_face(path))
        except (OSError, TTLibError) as exc:
            log.warning(
                "pdf_forms.fonts.extra_font_paths: skipping {} ({}); text "
                "needing it will be refused as unrenderable.",
                path,
                exc,
            )
    return tuple(faces)


def font_chain() -> tuple[FontFace, ...]:
    """The active face chain: bundled subsets, then configured extras."""
    return _load_chain(
        BUNDLED_FONTS, tuple(global_config.pdf_forms.fonts.extra_font_paths)
    )


def _face_for(char: str, chain: tuple[FontFace, ...]) -> FontFace | None:
    code = ord(char)
    return next((face for face in chain if code in face.cmap), None)


def uncovered_chars(text: str) -> str:
    """Characters in ``text`` that no face in the chain can draw, deduped."""
    chain = font_chain()
    seen: dict[str, None] = {}
    for char in text:
        if _face_for(char, chain) is None:
            seen.setdefault(char)
    return "".join(seen)


def _detach_trailing_neutrals(runs: list[TextRun]) -> list[TextRun]:
    """Move neutrals that end an RTL run into their own LTR run.

    Base direction is LTR (stamps are left-anchored), so in
    "by محمد on" the space after the name belongs between the name and
    "on", not inside the reversed Arabic run.
    """
    out: list[TextRun] = []
    for run in runs:
        cut = len(run.text)
        while run.rtl and cut and _is_detachable(run.text[cut - 1]):
            cut -= 1
        if cut:
            out.append(TextRun(run.face, run.text[:cut], run.rtl))
        if cut < len(run.text):
            out.append(TextRun(run.face, run.text[cut:], rtl=False))
    return out


def _is_detachable(char: str) -> bool:
    # Combining marks stay with their base character.
    bidi = unicodedata.bidirectional(char)
    return bidi in _NEUTRAL_CLASSES and bidi != "NSM"


def _is_neutral_run(run: TextRun) -> bool:
    return all(_is_detachable(c) for c in run.text)


def _visual_order(runs: list[TextRun]) -> list[TextRun]:
    """Reverse each RTL span so runs come out in left-to-right drawing order.

    A span is a maximal sequence that starts and ends with an RTL run and
    holds only RTL or neutral runs between (e.g. a space the Arabic face
    lacks), so a multi-word RTL name keeps its word order on the page.
    Each run's own glyphs are already in visual order from HarfBuzz.
    """
    out: list[TextRun] = []
    i = 0
    while i < len(runs):
        if not runs[i].rtl:
            out.append(runs[i])
            i += 1
            continue
        end = i
        j = i + 1
        while j < len(runs) and (runs[j].rtl or _is_neutral_run(runs[j])):
            if runs[j].rtl:
                end = j
            j += 1
        out.extend(reversed(runs[i : end + 1]))
        i = end + 1
    return out


def split_runs(text: str) -> list[TextRun]:
    """Split ``text`` into shaping runs (face + direction) in drawing order.

    Base direction is left-to-right; embedded RTL spans are reordered (see
    :func:`_visual_order`). Callers must have validated with
    :func:`uncovered_chars`; an uncovered character here is a programming
    error.
    """
    chain = font_chain()
    runs: list[TextRun] = []
    for char in text:
        bidi = unicodedata.bidirectional(char)
        current = runs[-1] if runs else None
        if (
            current is not None
            and bidi in _NEUTRAL_CLASSES
            and ord(char) in current.face.cmap
        ):
            runs[-1] = TextRun(current.face, current.text + char, current.rtl)
            continue
        face = _face_for(char, chain)
        if face is None:
            raise ValueError(f"no font in the chain covers {char!r}")
        rtl = bidi in _RTL_CLASSES
        if current is not None and current.face is face and current.rtl == rtl:
            runs[-1] = TextRun(face, current.text + char, rtl)
        else:
            runs.append(TextRun(face, char, rtl))
    return _visual_order(_detach_trailing_neutrals(runs))
