#!/usr/bin/env python3
"""Generate the default social-share image: public/og.png (1200x630).

Dev-only helper (the committed PNG is what ships). The Railway build runs
`bun run build`, which does NOT run this - regenerate locally after editing the
brand copy, then commit the new public/og.png:

    uv run --with pillow python scripts/gen-og.py

Colors mirror the @theme tokens in src/styles/global.css and the copy mirrors
src/config/landing.ts, so the card stays on-brand with the rest of the site.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --- brand tokens (keep in sync with src/styles/global.css @theme) ----------
BG = (0, 0, 0)
GRID = (20, 20, 20)
BORDER = (56, 56, 56)
FG = (255, 255, 255)
FG_MUTED = (155, 164, 166)
ACCENT = (195, 255, 253)  # Core Cyan 500

# --- copy (keep in sync with src/config/landing.ts) -------------------------
EYEBROW = "MCP SERVER STARTER"
WORDMARK = "GmailMCP"
HEADLINE = ["Give your AI agent", "real tools."]
SUBHEAD = "One service registry, exposed identically over CLI, MCP, and HTTP."
PILLS = ["CLI", "MCP", "HTTP"]
REPO = "github.com/Miyamura80/MCP-Template"

W, H = 1200, 630
PAD = 80

ARCHIVO_URL = (
    "https://github.com/google/fonts/raw/main/ofl/archivo/Archivo%5Bwdth,wght%5D.ttf"
)
FONT_CACHE = Path("/tmp/Archivo-variable.ttf")


def archivo(size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    if not FONT_CACHE.exists():
        urllib.request.urlretrieve(ARCHIVO_URL, FONT_CACHE)
    font = ImageFont.truetype(str(FONT_CACHE), size)
    try:
        # Archivo axes are [Weight, Width]; pin width to 100 (normal).
        font.set_variation_by_axes([weight, 100])
    except OSError:
        pass
    return font


def draw_tracked(draw, xy, text, font, fill, tracking):
    """Draw text with manual letter-spacing (Pillow has no native tracking)."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Faint grid, matching the hero's grid-bg.
    for gx in range(0, W, 60):
        d.line([(gx, 0), (gx, H)], fill=GRID, width=1)
    for gy in range(0, H, 60):
        d.line([(0, gy), (W, gy)], fill=GRID, width=1)

    # Eyebrow: cyan square + tracked uppercase label, like the hero pill.
    eb_font = archivo(22, 600)
    sq = 14
    eb_y = PAD
    d.rectangle([PAD, eb_y + 6, PAD + sq, eb_y + 6 + sq], fill=ACCENT)
    draw_tracked(d, (PAD + sq + 18, eb_y), EYEBROW, eb_font, FG_MUTED, 4)

    # Headline.
    hl_font = archivo(86, 800)
    y = 168
    for line in HEADLINE:
        d.text((PAD, y), line, font=hl_font, fill=FG)
        y += 96

    # Subhead.
    sh_font = archivo(30, 400)
    d.text((PAD, y + 24), SUBHEAD, font=sh_font, fill=FG_MUTED)

    # Bottom row: transport pills (left) + repo (right).
    pill_font = archivo(26, 600)
    px = PAD
    py = H - PAD - 44
    for label in PILLS:
        tw = d.textlength(label, font=pill_font)
        pw = tw + 44
        d.rounded_rectangle(
            [px, py, px + pw, py + 48], radius=8, outline=ACCENT, width=2
        )
        d.text((px + 22, py + 8), label, font=pill_font, fill=ACCENT)
        px += pw + 16

    repo_font = archivo(24, 500)
    rw = d.textlength(REPO, font=repo_font)
    d.text((W - PAD - rw, py + 12), REPO, font=repo_font, fill=FG_MUTED)

    # Wordmark, top-right.
    wm_font = archivo(34, 700)
    ww = d.textlength(WORDMARK, font=wm_font)
    d.text((W - PAD - ww, PAD - 4), WORDMARK, font=wm_font, fill=FG)

    out = Path(__file__).resolve().parent.parent / "public" / "og.png"
    img.save(out, "PNG")
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
