# /// script
# requires-python = ">=3.12"
# dependencies = ["fonttools>=4.33.3"]
# ///
"""Regenerate the subsetted Noto fonts vendored in ``services/pdf_fonts/``.

The PDF stamp/overlay path embeds a per-document subset of these files, so
what is vendored only bounds which characters *can* render. Full Noto CJK is
~16 MB per face; these subsets keep the whole bundle near 3 MB while covering
the scripts signer names are realistically written in:

- ``NotoSans``: Latin (incl. Vietnamese), Greek, Cyrillic, punctuation.
- ``NotoSansArabic``: Arabic + presentation forms (shaped by HarfBuzz).
- ``NotoSansJP``: kana + JIS X 0208 level-1 kanji + the GB2312 level-1
  hanzi this face has (Japanese glyph shapes take priority).
- ``NotoSansSC``: the remaining GB2312 level-1 hanzi (simplified forms).
- ``NotoSansKR``: the 2,350 KS X 1001 Hangul syllables + compatibility jamo.

Anything else (Hebrew, Indic, Thai, rarer CJK) is refused with a clear error
unless an operator adds a font via ``pdf_forms.fonts.extra_font_paths``.

Sources are the static 400-weight TTFs Google Fonts publishes, taken from the
``@expo-google-fonts/<family>`` npm tarballs (e.g.
``https://registry.npmjs.org/@expo-google-fonts/noto-sans-jp/-/noto-sans-jp-0.4.4.tgz``,
file ``package/400Regular/NotoSansJP_400Regular.ttf``). All are SIL OFL 1.1;
subsetting is a permitted modification and none of the family names collide
with a Reserved Font Name (the CJK faces reserve only "Source").

Usage::

    uv run scripts/build_pdf_fonts.py <dir with the five *_400Regular.ttf>
"""

from __future__ import annotations

import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

OUT_DIR = Path(__file__).resolve().parent.parent / "services" / "pdf_fonts"


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
    print(f"{out.name}: {len(wanted)} code points, {out.stat().st_size:,} bytes")


def main(src_dir: Path) -> None:
    def src(family: str) -> Path:
        return src_dir / f"{family}_400Regular.ttf"

    _subset(src("NotoSans"), LATIN, OUT_DIR / "NotoSans-Regular.subset.ttf")
    _subset(
        src("NotoSansArabic"), ARABIC, OUT_DIR / "NotoSansArabic-Regular.subset.ttf"
    )
    jp_cmap = _cmap(src("NotoSansJP"))
    japanese = JAPANESE | (GB2312_LEVEL1 & jp_cmap)
    _subset(src("NotoSansJP"), japanese, OUT_DIR / "NotoSansJP-Regular.subset.ttf")
    _subset(
        src("NotoSansSC"),
        GB2312_LEVEL1 - jp_cmap,
        OUT_DIR / "NotoSansSC-Regular.subset.ttf",
    )
    _subset(src("NotoSansKR"), KOREAN, OUT_DIR / "NotoSansKR-Regular.subset.ttf")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
