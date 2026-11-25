"""Generate the README's SVG diagrams into media/.

Run: uv run python scripts/gen_readme_diagrams.py

Layout lives in named constants and small render helpers so edits reflow
instead of being hand-placed. Each SVG carries its own light/dark palette via
`prefers-color-scheme`, so one file works in both GitHub themes. Logos are the
real marks vendored in media/icons/ (eito.me/icons, svgl.app, simpleicons.org).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "media"
ICONS = MEDIA / "icons"

FONT = "ui-sans-serif, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

STYLE = f"""
  :root {{ --fg:#1f2328; --muted:#656d76; --line:#d0d7de; --card:#ffffff;
           --soft:#f6f8fa; --accent:#5b4ee6; --accent-soft:#efedff; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --fg:#e6edf3; --muted:#9198a1; --line:#3d444d; --card:#151b23;
             --soft:#0d1117; --accent:#9d94ff; --accent-soft:#221f45; }}
  }}
  text {{ font-family:{FONT}; fill:var(--fg); }}
  .mono {{ font-family:{MONO}; }}
  .muted {{ fill:var(--muted); }}
  .accent {{ fill:var(--accent); }}
  .card {{ fill:var(--card); stroke:var(--line); stroke-width:1.5; }}
  .hero {{ fill:var(--accent-soft); stroke:var(--accent); stroke-width:2; }}
  .wire {{ stroke:var(--line); stroke-width:2; fill:none; }}
  .wire-accent {{ stroke:var(--accent); stroke-width:2; fill:none; }}
  .mono-icon {{ fill:var(--fg); color:var(--fg); }}
"""

# Icons whose mark is single-colour get recoloured to the theme foreground.
MONO_ICONS = {"mcp", "chatgpt", "goose", "typer"}


def _symbol(name: str) -> str:
    raw = (ICONS / f"{name}.svg").read_text()
    m = re.search(r'viewBox="([^"]+)"', raw)
    if m is None:
        raise ValueError(f"media/icons/{name}.svg has no viewBox")
    view_box = m.group(1)
    inner = re.sub(r"^.*?<svg[^>]*>|</svg>\s*$", "", raw, flags=re.S)
    inner = re.sub(r"<title>.*?</title>", "", inner)
    if name in MONO_ICONS:
        inner = re.sub(r'\sfill="(?!none|url)[^"]*"', "", inner)
        cls = ' class="mono-icon"'
    else:
        cls = ""
    # Namespace gradient ids so several symbols can share one document.
    inner = re.sub(r'id="([^"]+)"', rf'id="{name}-\1"', inner)
    inner = re.sub(r"url\(#([^)]+)\)", rf"url(#{name}-\1)", inner)
    return f'<symbol id="i-{name}" viewBox="{view_box}"{cls}>{inner}</symbol>'


def icon(name: str, x: float, y: float, size: float) -> str:
    return f'<use href="#i-{name}" x="{x}" y="{y}" width="{size}" height="{size}"/>'


def text(x, y, s, size=14, cls="", anchor="middle", weight=400) -> str:
    c = f' class="{cls}"' if cls else ""
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" '
        f'font-weight="{weight}"{c}>{s}</text>'
    )


def arrow(x1, y1, x2, y2, cls="wire") -> str:
    return (
        f'<path class="{cls}" d="M{x1} {y1} L{x2} {y2}" marker-end="url(#{cls}-head)"/>'
    )


def curve(x1, y1, x2, y2, cls="wire") -> str:
    my = (y1 + y2) / 2
    return (
        f'<path class="{cls}" d="M{x1} {y1} C{x1} {my} {x2} {my} {x2} {y2}" '
        f'marker-end="url(#{cls}-head)"/>'
    )


def doc(w: int, h: int, icons: list[str], body: list[str], title: str) -> str:
    heads = "".join(
        f'<marker id="{c}-head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" '
        f'style="fill:var(--{col})"/></marker>'
        for c, col in (("wire", "line"), ("wire-accent", "accent"))
    )
    syms = "".join(_symbol(n) for n in icons)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" '
        f'height="{h}" role="img" aria-label="{title}"><title>{title}</title>'
        f"<style>{STYLE}</style><defs>{heads}{syms}</defs>" + "".join(body) + "</svg>\n"
    )


# ---------------------------------------------------------------- architecture
def architecture() -> str:
    w, h = 880, 470
    cols = [150, 440, 730]  # lane centres
    card_w, card_h = 250, 112
    y_client, y_card, y_svc, y_infra = 28, 118, 300, 418
    svc_w, svc_h = 520, 76

    lanes = [
        # (client logos or a plain glyph, client label, logo, title, example call)
        ("&gt;_", "your terminal", "typer", "CLI", "mymcp greet Alice"),
        (
            ["claude", "chatgpt", "goose"],
            "AI clients",
            "mcp",
            "MCP server",
            "tools/call  greet",
        ),
        (
            "{ }",
            "apps &amp; scripts",
            "fastapi",
            "HTTP API",
            "POST /api/v1/services/greet",
        ),
    ]
    b: list[str] = []
    for cx, (clients, label, logo, title, call) in zip(cols, lanes, strict=True):
        # client row: real logos where the client is a product; a plain glyph otherwise
        if isinstance(clients, list):
            n, s, gap = len(clients), 26, 14
            x0 = cx - (n * s + (n - 1) * gap) / 2
            b += [
                icon(c, x0 + i * (s + gap), y_client, s) for i, c in enumerate(clients)
            ]
        else:
            b.append(text(cx, y_client + 20, clients, 20, "mono muted", weight=700))
        b.append(text(cx, y_client + 46, label, 12, "muted"))
        b.append(arrow(cx, y_client + 54, cx, y_card - 4))
        # interface card
        x = cx - card_w / 2
        b.append(
            f'<rect class="card" x="{x}" y="{y_card}" width="{card_w}" height="{card_h}" rx="12"/>'
        )
        b.append(icon(logo, cx - 15, y_card + 16, 30))
        b.append(text(cx, y_card + 68, title, 17, weight=650))
        b.append(text(cx, y_card + 92, call, 12, "mono muted"))
        b.append(
            curve(
                cx,
                y_card + card_h,
                w / 2 + (cx - w / 2) * 0.35,
                y_svc - 4,
                "wire-accent",
            )
        )

    sx = w / 2 - svc_w / 2
    b.append(
        f'<rect class="hero" x="{sx}" y="{y_svc}" width="{svc_w}" height="{svc_h}" rx="14"/>'
    )
    b.append(icon("python", sx + 22, y_svc + svc_h / 2 - 15, 30))
    b.append(
        text(
            w / 2 + 14,
            y_svc + 32,
            "@service  greet(GreetInput) → GreetResult",
            16,
            "mono",
            weight=600,
        )
    )
    b.append(
        text(
            w / 2 + 14,
            y_svc + 56,
            "written once in services/  ·  one Pydantic contract",
            13,
            "accent",
        )
    )

    b.append(arrow(w / 2, y_svc + svc_h, w / 2, y_infra - 4))
    b.append(
        f'<rect x="{sx}" y="{y_infra}" width="{svc_w}" height="34" rx="8" '
        f'style="fill:var(--soft);stroke:var(--line)"/>'
    )
    b.append(
        text(
            w / 2,
            y_infra + 22,
            "config  ·  database  ·  auth &amp; billing  ·  LLM",
            13,
            "muted",
        )
    )
    icons = ["typer", "claude", "chatgpt", "goose", "mcp", "fastapi", "python"]
    return doc(
        w, h, icons, b, "One @service, three interfaces: CLI, MCP server, HTTP API"
    )


# -------------------------------------------------------------------- MCP apps
def mcp_apps() -> str:
    canvas_w, canvas_h = 880, 300
    svc = (40, 112, 190, 76)  # x, y, w, h
    rows = [62, 150, 238]  # output row centres
    x_lane, x_out = 300, 520
    b: list[str] = []
    x, y, w, h = svc
    b.append(f'<rect class="card" x="{x}" y="{y}" width="{w}" height="{h}" rx="12"/>')
    b.append(text(x + w / 2, y + 34, "@service", 16, "mono", weight=650))
    b.append(text(x + w / 2, y + 56, "unchanged", 12, "muted"))

    plain = [("typer", "CLI", "table / json"), ("fastapi", "HTTP API", "JSON")]
    for cy, (logo, name, out) in zip((rows[0], rows[2]), plain, strict=True):
        b.append(curve(x + w, y + h / 2, x_lane - 6, cy, "wire"))
        b.append(icon(logo, x_lane, cy - 13, 26))
        b.append(text(x_lane + 38, cy + 5, name, 15, anchor="start", weight=600))
        b.append(arrow(x_lane + 140, cy, x_out - 8, cy))
        b.append(text(x_out, cy + 5, out, 13, "mono muted", anchor="start"))

    cy = rows[1]
    b.append(arrow(x + w, cy, x_lane - 6, cy, "wire-accent"))
    b.append(icon("mcp", x_lane, cy - 13, 26))
    b.append(
        text(x_lane + 38, cy - 2, "MCP + enhancer", 15, anchor="start", weight=600)
    )
    b.append(
        text(
            x_lane + 38,
            cy + 16,
            "elicit · images · ui://",
            12,
            "accent",
            anchor="start",
        )
    )
    b.append(arrow(x_lane + 180, cy, x_out - 8, cy, "wire-accent"))
    # chat window with an embedded app iframe
    cw, ch = 330, 150
    cx0, cy0 = x_out, cy - ch / 2
    b.append(
        f'<rect class="card" x="{cx0}" y="{cy0}" width="{cw}" height="{ch}" rx="12"/>'
    )
    b.append(icon("claude", cx0 + 14, cy0 + 12, 18))
    b.append(
        f'<rect x="{cx0 + 40}" y="{cy0 + 16}" width="150" height="10" rx="5" style="fill:var(--line)"/>'
    )
    fx, fy, fw, fh = cx0 + 14, cy0 + 40, cw - 28, ch - 54
    b.append(
        f'<rect class="hero" x="{fx}" y="{fy}" width="{fw}" height="{fh}" rx="8"/>'
    )
    b.append(
        text(
            fx + 14,
            fy + 24,
            "interactive app (iframe)",
            13,
            "accent",
            anchor="start",
            weight=600,
        )
    )
    for i, wd in enumerate((210, 160, 190)):
        b.append(
            f'<rect x="{fx + 14}" y="{fy + 40 + i * 14}" width="{wd}" height="7" rx="3.5" '
            f'style="fill:var(--accent);opacity:.35"/>'
        )
    return doc(
        canvas_w,
        canvas_h,
        ["typer", "fastapi", "mcp", "claude"],
        b,
        "Enhancers add UI on the MCP transport only",
    )


# ----------------------------------------------------------------- add feature
def add_feature() -> str:
    w, h = 880, 150
    step_w, step_h, y = 220, 84, 34
    xs = [40, 330]
    steps = [
        ("1", "models/", "Pydantic input + output"),
        ("2", "services/", "@service function"),
    ]
    b: list[str] = []
    for x, (n, folder, what) in zip(xs, steps, strict=True):
        b.append(
            f'<rect class="card" x="{x}" y="{y}" width="{step_w}" height="{step_h}" rx="12"/>'
        )
        b.append(
            f'<circle cx="{x + 30}" cy="{y + step_h / 2}" r="14" style="fill:var(--accent)"/>'
        )
        b.append(
            f'<text x="{x + 30}" y="{y + step_h / 2 + 5}" font-size="14" text-anchor="middle" '
            f'font-weight="700" style="fill:var(--card)">{n}</text>'
        )
        b.append(text(x + 58, y + 36, folder, 16, "mono", anchor="start", weight=650))
        b.append(text(x + 58, y + 58, what, 12, "muted", anchor="start"))
    b.append(arrow(xs[0] + step_w + 6, y + step_h / 2, xs[1] - 8, y + step_h / 2))
    b.append(
        arrow(xs[1] + step_w + 6, y + step_h / 2, 616, y + step_h / 2, "wire-accent")
    )
    # result: the three interfaces light up, nothing else to wire
    rx = 626
    b.append(
        f'<rect class="hero" x="{rx}" y="{y}" width="214" height="{step_h}" rx="12"/>'
    )
    for i, logo in enumerate(("typer", "mcp", "fastapi")):
        b.append(icon(logo, rx + 30 + i * 60, y + 16, 28))
    b.append(text(rx + 107, y + 68, "CLI · MCP · API, auto-wired", 12, "accent"))
    return doc(
        w,
        h,
        ["typer", "mcp", "fastapi"],
        b,
        "Add a feature: models, then a @service; all three interfaces pick it up",
    )


def main() -> None:
    out = {
        "architecture.svg": architecture(),
        "mcp-apps.svg": mcp_apps(),
        "add-feature.svg": add_feature(),
    }
    for name, svg in out.items():
        (MEDIA / name).write_text(svg)
        print(f"wrote media/{name}")


if __name__ == "__main__":
    main()
