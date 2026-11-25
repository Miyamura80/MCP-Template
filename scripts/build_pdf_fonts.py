# /// script
# requires-python = ">=3.12"
# dependencies = ["fonttools==4.68.0"]
# ///
"""Regenerate the subsetted Noto fonts vendored in ``services/pdf_fonts/``.

The PDF stamp/overlay path embeds a per-document subset of these files, so
what is vendored only bounds which characters *can* render. Full Noto CJK is
~16 MB per face; these subsets keep the whole bundle near 3 MB while covering
the scripts signer names are realistically written in.

Bundled (committed, ~2 MB):

- ``NotoSans``: Latin (incl. Vietnamese), Greek, Cyrillic, punctuation.
- ``NotoSansArabic``: Arabic + presentation forms (shaped by HarfBuzz).
- ``NotoSansJP``: kana + JIS X 0208 level-1 kanji + the GB2312 level-1
  hanzi this face has (Japanese glyph shapes take priority).

Opt-in (``--cjk``, run by ``make pdf_fonts_cjk``; written to the gitignored
``services/pdf_fonts/optional/`` and loaded automatically when present):

- ``NotoSansSC``: the GB2312 level-1 hanzi the JP subset lacks (simplified).
- ``NotoSansKR``: the 2,350 KS X 1001 Hangul syllables + compatibility jamo.

Anything else (Hebrew, Indic, Thai, rarer CJK) is refused with a clear error
unless an operator adds a font via ``pdf_forms.fonts.extra_font_paths``.

Sources are the static 400-weight TTFs Google Fonts serves, as packaged in
the version-pinned ``@expo-google-fonts/<family>`` npm tarballs listed in
``SOURCES`` (google/fonts itself ships only variable fonts, so it has no
byte-identical static file to pin). Each download is verified against the
recorded sha256 before subsetting. All are SIL OFL 1.1; subsetting is a
permitted modification and none of the family names collide with a Reserved
Font Name (the CJK faces reserve only "Source").

The output is reproducible (pinned fontTools, no timestamp recalculation).
After a deliberate change, update the sha256 values this script prints in
``BUNDLED_FONTS`` / ``OPTIONAL_FONTS`` (``services/pdf_fonts/__init__.py``);
``tests/test_pdf_unicode_fonts.py`` checks the vendored files against them,
and the font chain ignores an optional file whose hash does not match.

Usage::

    uv run scripts/build_pdf_fonts.py         # regenerate the bundled fonts
    uv run scripts/build_pdf_fonts.py --cjk   # build the opt-in SC/KR fonts
"""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

OUT_DIR = Path(__file__).resolve().parent.parent / "services" / "pdf_fonts"
OPTIONAL_OUT_DIR = OUT_DIR / "optional"

_NPM = "https://registry.npmjs.org/@expo-google-fonts"

# family -> (tarball URL, member path, sha256 of that TTF)
SOURCES: dict[str, tuple[str, str, str]] = {
    "NotoSans": (
        f"{_NPM}/noto-sans/-/noto-sans-0.4.2.tgz",
        "package/400Regular/NotoSans_400Regular.ttf",
        "fe8c022f48d8dd29f17b744d16f9346f4357e16f7d4f7be58b000ae7c291b614",
    ),
    "NotoSansArabic": (
        f"{_NPM}/noto-sans-arabic/-/noto-sans-arabic-0.4.3.tgz",
        "package/400Regular/NotoSansArabic_400Regular.ttf",
        "252629ca0e87b6233851249b8cbf7b43445211a8caf199f1b306a19202251508",
    ),
    "NotoSansJP": (
        f"{_NPM}/noto-sans-jp/-/noto-sans-jp-0.4.4.tgz",
        "package/400Regular/NotoSansJP_400Regular.ttf",
        "6255ea4692e97ef27330304fb96fd650f9111f868cd585fcf2802d42823930e5",
    ),
    "NotoSansSC": (
        f"{_NPM}/noto-sans-sc/-/noto-sans-sc-0.4.4.tgz",
        "package/400Regular/NotoSansSC_400Regular.ttf",
        "d45f67f0a7c0ca3f256950777ce6a61cc7ce5f9696d02900cbbaac25f8aa7d16",
    ),
    "NotoSansKR": (
        f"{_NPM}/noto-sans-kr/-/noto-sans-kr-0.4.4.tgz",
        "package/400Regular/NotoSansKR_400Regular.ttf",
        "b4a747e78805bc25b610dcfc3003cb5fb2e2ec034763774b24216ed15889eef4",
    ),
}


def _span(lo: int, hi: int) -> set[int]:
    return set(range(lo, hi + 1))


def _dbcs_rows(codec: str, first: int, last: int) -> set[int]:
    """Code points of a double-byte charset's lead-byte rows first..last."""
    out: set[int] = set()
    for lead in range(first, last + 1):
        for trail in range(0xA1, 0xFF):
            try:
                out.add(ord(bytes([lead, trail]).decode(codec)))
            except UnicodeDecodeError:
                continue
    return out


JIS_LEVEL1 = _dbcs_rows("euc_jp", 0xB0, 0xCF)  # 2,965 kanji
GB2312_LEVEL1 = _dbcs_rows("gb2312", 0xB0, 0xD7)  # 3,755 hanzi
KSX1001_HANGUL = _dbcs_rows("euc_kr", 0xB0, 0xC8)  # 2,350 syllables
CJK_COMMON = _span(0x3000, 0x303F) | _span(0xFF00, 0xFFEF)

LATIN = (
    _span(0x20, 0x24F)
    | _span(0x300, 0x36F)
    | _span(0x370, 0x3FF)
    | _span(0x400, 0x52F)
    | _span(0x1E00, 0x1FFF)
    | _span(0x2000, 0x206F)
    | _span(0x20A0, 0x20C0)
    | _span(0x2100, 0x214F)
)
ARABIC = (
    _span(0x600, 0x6FF)
    | _span(0x750, 0x77F)
    | _span(0x8A0, 0x8FF)
    | _span(0xFB50, 0xFDFF)
    | _span(0xFE70, 0xFEFF)
    | {0x200C, 0x200D}
)
JAPANESE = CJK_COMMON | _span(0x3040, 0x30FF) | _span(0x31F0, 0x31FF) | JIS_LEVEL1
KOREAN = KSX1001_HANGUL | _span(0x3130, 0x318F)


def _cmap(path: Path) -> set[int]:
    return set(TTFont(path).getBestCmap() or {})


def _subset(src: Path, unicodes: set[int], out: Path) -> None:
    font = TTFont(src, recalcTimestamp=False)  # reproducible output
    wanted = unicodes & set(font.getBestCmap() or {})
    options = subset.Options(
        layout_features=["*"],  # keep shaping features (Arabic joining)
        name_IDs=["*"],  # keep copyright/license records
        notdef_outline=True,
        hinting=False,  # pointless in a PDF and ~10% of the size
    )
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=wanted)
    subsetter.subset(font)
    font.save(out)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    size = out.stat().st_size
    print(f"{out.name}: {len(wanted)} code points, {size:,} bytes, sha256 {digest}")


def _fetch(family: str, dest: Path) -> Path:
    """Download one source TTF and refuse it unless its sha256 matches."""
    url, member, expected = SOURCES[family]
    with urllib.request.urlopen(url, timeout=120) as response:
        archive = response.read()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        extracted = tar.extractfile(member)
        if extracted is None:
            sys.exit(f"{url}: {member} missing from the tarball")
        data = extracted.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        sys.exit(f"{family}: sha256 mismatch (expected {expected}, got {actual})")
    path = dest / f"{family}.ttf"
    path.write_bytes(data)
    return path


def _build_bundled(tmp: Path) -> None:
    _subset(_fetch("NotoSans", tmp), LATIN, OUT_DIR / "NotoSans-Regular.subset.ttf")
    _subset(
        _fetch("NotoSansArabic", tmp),
        ARABIC,
        OUT_DIR / "NotoSansArabic-Regular.subset.ttf",
    )
    jp = _fetch("NotoSansJP", tmp)
    japanese = JAPANESE | (GB2312_LEVEL1 & _cmap(jp))
    _subset(jp, japanese, OUT_DIR / "NotoSansJP-Regular.subset.ttf")


def _build_cjk(tmp: Path) -> None:
    OPTIONAL_OUT_DIR.mkdir(exist_ok=True)
    # Only the hanzi the bundled JP subset cannot draw (JP shapes win).
    jp_cmap = _cmap(_fetch("NotoSansJP", tmp))
    _subset(
        _fetch("NotoSansSC", tmp),
        GB2312_LEVEL1 - jp_cmap,
        OPTIONAL_OUT_DIR / "NotoSansSC-Regular.subset.ttf",
    )
    _subset(
        _fetch("NotoSansKR", tmp),
        KOREAN,
        OPTIONAL_OUT_DIR / "NotoSansKR-Regular.subset.ttf",
    )


def main(argv: list[str]) -> None:
    if argv not in ([], ["--cjk"]):
        sys.exit(__doc__)
    with tempfile.TemporaryDirectory() as tmp:
        if argv:
            _build_cjk(Path(tmp))
        else:
            _build_bundled(Path(tmp))


if __name__ == "__main__":
    main(sys.argv[1:])
