"""Unicode font chain for PDF overlay text and the signature stamp.

Part of the PDF core (isolation seam): no Gmail imports.

Text outside Latin-1 cannot be drawn with the standard-14 faces, so
:mod:`services.pdf_overlay` embeds a subset of a real font for it. This
module owns *which* font: an ordered chain of bundled Noto subsets (see
``OFL.txt`` and ``scripts/build_pdf_fonts.py`` for provenance and coverage)
followed by any operator-supplied ``pdf_forms.fonts.extra_font_paths``. Each
character goes to the first face whose cmap has it; text is then split into
runs of one face and one bidi level (:mod:`services.pdf_fonts.bidi`) so
HarfBuzz can shape each run (Arabic joining, right-to-left glyph order).

The bundled subsets are a size trade-off: full Noto CJK is ~16 MB per face,
these total ~2 MB and cover Latin, Greek, Cyrillic, Arabic and Japanese.
Chinese and Korean subsets are opt-in: ``make pdf_fonts_cjk`` downloads and
builds them into :data:`OPTIONAL_DIR` (gitignored), and the chain picks them
up automatically when their sha256 matches :data:`OPTIONAL_FONTS`. Rarer
ideographs and uncovered scripts are reported by :func:`uncovered_chars` so
callers refuse them up front instead of stamping a ``.notdef`` box.
"""

from __future__ import annotations

import functools
import hashlib
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from fontTools.ttLib import TTFont, TTLibError
from loguru import logger as log

from common import global_config
from common.global_config import root_dir
from services.pdf_fonts.bidi import resolve_levels, visual_order

FONT_DIR = Path(__file__).resolve().parent

# file name -> sha256 of the vendored file (printed by
# scripts/build_pdf_fonts.py; tests check the files against these). Chain
# order matters: the first face covering a character wins. Latin goes first
# so mixed lines ("Signed by ... on <date>") keep one Latin face; JP precedes
# the optional SC face so shared ideographs get Japanese glyph shapes.
BUNDLED_FONTS: dict[str, str] = {
    "NotoSans-Regular.subset.ttf": (
        "261f2fe0e4352afca566e692b80e4be188f7cda6da2a0fb97ddae688e341df58"
    ),
    "NotoSansArabic-Regular.subset.ttf": (
        "33c25d28e196d5f321cac1f6124d1fc6595e68b9964d3cf8a83c44676b7bde58"
    ),
    "NotoSansJP-Regular.subset.ttf": (
        "1fd9e1356a1374e8718c98f9dc13d045fafbf1da6618a6595724869089fc1b67"
    ),
}

# Opt-in CJK subsets built by `make pdf_fonts_cjk` (scripts/build_pdf_fonts.py
# --cjk) into a gitignored directory: file name -> (sha256, what it adds to
# the coverage summary). Loaded after the bundled faces when present and
# matching; the build is reproducible, so the hashes are stable.
OPTIONAL_DIR = FONT_DIR / "optional"
OPTIONAL_FONTS: dict[str, tuple[str, str]] = {
    "NotoSansSC-Regular.subset.ttf": (
        "aa1decd3d6904ff05f654af09e6eb515e4f5ad3c1e8d569f67104f68f55355ec",
        "common simplified Chinese hanzi",
    ),
    "NotoSansKR-Regular.subset.ttf": (
        "9ea969fb35753c6d01a722fa1a0fb2b5f5627d72de6191621ac06401e03424e6",
        "common Korean Hangul",
    ),
}

# What the bundled fonts cover, for user-facing errors; see coverage_summary().
# Keep in step with scripts/build_pdf_fonts.py.
_BUNDLED_COVERAGE = (
    "Latin (incl. Vietnamese), Greek, Cyrillic, Arabic, Japanese kana and common kanji"
)

# Characters that ride along with the previous character's face when it has
# them, so "محمد علي" is not split at the space by face changes.
_FACE_NEUTRAL = frozenset({"WS", "ON", "CS", "ES", "ET", "BN", "NSM", "B", "S"})


@dataclass(frozen=True, eq=False)
class FontFace:
    """One font file in the chain: its bytes (for embedding) and cmap.

    Identity-compared: faces are loaded once per chain and used as keys.
    """

    path: Path
    data: bytes
    cmap: frozenset[int]


@dataclass(frozen=True)
class TextRun:
    """A maximal slice of text drawn with one face at one bidi level."""

    face: FontFace
    text: str
    level: int

    @property
    def rtl(self) -> bool:
        return self.level % 2 == 1


def _load_face(path: Path) -> FontFace:
    data = path.read_bytes()
    cmap = TTFont(path, lazy=True).getBestCmap() or {}
    return FontFace(path=path, data=data, cmap=frozenset(cmap))


def _load_optional(directory: Path, name: str, sha256: str) -> FontFace | None:
    path = directory / name
    if not path.is_file():
        return None
    try:
        face = _load_face(path)
    except (OSError, TTLibError) as exc:
        log.warning("Optional PDF font {} failed to load: {}", path, exc)
        return None
    if hashlib.sha256(face.data).hexdigest() != sha256:
        log.warning(
            "Optional PDF font {} does not match its pinned sha256; skipping "
            "it. Re-run `make pdf_fonts_cjk`.",
            path,
        )
        return None
    return face


@functools.cache
def _load_chain(
    bundled: tuple[str, ...],
    optional_dir: Path,
    optional: tuple[tuple[str, str], ...],
    extra_paths: tuple[str, ...],
) -> tuple[FontFace, ...]:
    faces: list[FontFace] = []
    for name in bundled:
        try:
            faces.append(_load_face(FONT_DIR / name))
        except (OSError, TTLibError) as exc:
            # A broken install (fonts stripped from the wheel) must be loud:
            # every non-Latin-1 name would be refused without an obvious cause.
            log.error("Bundled PDF font {} failed to load: {}", name, exc)
    for name, sha256 in optional:
        if (face := _load_optional(optional_dir, name, sha256)) is not None:
            faces.append(face)
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
    """The active face chain: bundled, opt-in CJK, then configured extras.

    Cached per configuration; also called at server startup so a broken
    install or bad ``extra_font_paths`` entry is logged at boot.
    """
    return _load_chain(
        tuple(BUNDLED_FONTS),
        OPTIONAL_DIR,
        tuple((name, sha) for name, (sha, _) in OPTIONAL_FONTS.items()),
        tuple(global_config.pdf_forms.fonts.extra_font_paths),
    )


def coverage_summary() -> str:
    """Human-readable list of what the active chain covers, for errors."""
    loaded = {face.path.name for face in font_chain()}
    parts = [_BUNDLED_COVERAGE]
    parts += [label for name, (_, label) in OPTIONAL_FONTS.items() if name in loaded]
    return ", ".join(parts)


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


def split_runs(text: str) -> list[TextRun]:
    """Split ``text`` into shaping runs (face + bidi level) in drawing order.

    Callers must have validated with :func:`uncovered_chars`; an uncovered
    character here is a programming error.
    """
    chain = font_chain()
    levels = resolve_levels(text)
    runs: list[TextRun] = []
    for char, level in zip(text, levels, strict=True):
        current = runs[-1] if runs else None
        if (
            current is not None
            and unicodedata.bidirectional(char) in _FACE_NEUTRAL
            and ord(char) in current.face.cmap
        ):
            face = current.face
        else:
            face = _face_for(char, chain)
            if face is None:
                raise ValueError(f"no font in the chain covers {char!r}")
        if current is not None and current.face is face and current.level == level:
            runs[-1] = TextRun(face, current.text + char, level)
        else:
            runs.append(TextRun(face, char, level))
    return [runs[i] for i in visual_order([run.level for run in runs])]
