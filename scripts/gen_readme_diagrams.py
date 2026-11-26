"""Generate the README's SVG diagrams into media/ in the SealGate Hackbox style.

Run: make gen_readme_diagrams   (check: make readme_diagrams_check)

Brand rules applied here (see the sealgate-brand skill): Baseline Black ground,
Core Cyan wireframes for primary flow, Graphene Grey for secondary, white type
in a neo-grotesque sans, square corners, no gradients or shadows, and every
container is a Hackbox: a solid tag inside the top-left corner sized 40% of the
box's width by 10% of its height. Logos are the real marks vendored in
media/icons/ (eito.me/icons, svgl.app, simpleicons.org), recoloured to white.

The canvas is drawn on its own black ground, so the files read the same in
GitHub's light and dark themes. Positions derive from the named constants at
the top of each figure function.
"""

from __future__ import annotations

import html
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "media"
ICONS = MEDIA / "icons"
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

BLACK = "#000000"
CYAN = "#C3FFFD"
GREY = "#9BA4A6"  # Graphene Grey: secondary text
WIRE = "#5E6575"  # Graphene Grey (main): secondary strokes
WHITE = "#FFFFFF"
FONT = "Inter, Helvetica, Roboto, Arial, sans-serif"
MONO = "'JetBrains Mono', 'IBM Plex Mono', ui-monospace, Menlo, monospace"
GRID = 40  # invisible layout grid; markers sit on its intersections
STROKE = 1.5

STYLE = f"""
  text {{ font-family:{FONT}; fill:{WHITE}; }}
  .mono {{ font-family:{MONO}; }}
  .muted {{ fill:{GREY}; }}
  .accent {{ fill:{CYAN}; }}
  .box {{ fill:none; stroke:{CYAN}; stroke-width:{STROKE}; }}
  .box-dim {{ fill:none; stroke:{WIRE}; stroke-width:{STROKE}; }}
  .tag {{ fill:{CYAN}; }}
  .tag-dim {{ fill:{WIRE}; }}
  .wire {{ stroke:{WIRE}; stroke-width:{STROKE}; fill:none; }}
  .wire-accent {{ stroke:{CYAN}; stroke-width:{STROKE}; fill:none; }}
  .logo {{ fill:{WHITE}; }}
"""

# Paint attributes dropped from logos so the symbol's white fill applies to
# every shape; fill-rule / clip-rule are kept because they carry the holes.
_PAINT_ATTRS = {"fill", "stroke", "style", "class", "opacity", "fill-opacity"}
_KEEP_ROOT_ATTRS = ("fill-rule", "clip-rule")


def _symbol(name: str) -> str:
    """Inline a vendored logo as a white, single-colour <symbol>."""
    root = ET.parse(ICONS / f"{name}.svg").getroot()
    view_box = root.get("viewBox")
    if not view_box:
        raise ValueError(f"media/icons/{name}.svg has no viewBox")
    shapes: list[str] = []
    for child in root:
        tag = child.tag.split("}")[-1]
        if tag in {"title", "defs", "desc", "metadata"}:
            continue
        for el in child.iter():
            for attr in _PAINT_ATTRS & set(el.attrib):
                del el.attrib[attr]
        shapes.append(ET.tostring(child, encoding="unicode"))
    inner = re.sub(r'\sxmlns(:\w+)?="[^"]*"', "", "".join(shapes))
    keep = "".join(f' {a}="{root.get(a)}"' for a in _KEEP_ROOT_ATTRS if root.get(a))
    return f'<symbol id="i-{name}" viewBox="{view_box}" class="logo"{keep}>{inner}</symbol>'


def icon(name: str, x: float, y: float, size: float) -> str:
    return f'<use href="#i-{name}" x="{x}" y="{y}" width="{size}" height="{size}"/>'


def text(
    x: float,
    y: float,
    s: str,
    size: int = 14,
    cls: str = "",
    anchor: str = "middle",
    weight: int = 400,
) -> str:
    c = f' class="{cls}"' if cls else ""
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" '
        f'font-weight="{weight}"{c}>{html.escape(s, quote=False)}</text>'
    )


def hackbox(x: float, y: float, w: float, h: float, dim: bool = False) -> str:
    """Wireframe box with the brand tag inside its top-left corner (40% x 10%)."""
    sfx = "-dim" if dim else ""
    inset = STROKE / 2
    return (
        f'<rect class="box{sfx}" x="{x}" y="{y}" width="{w}" height="{h}"/>'
        f'<rect class="tag{sfx}" x="{x + inset}" y="{y + inset}" '
        f'width="{w * 0.4}" height="{h * 0.1}"/>'
    )


def bar(x: float, y: float, w: float, h: float) -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{WIRE}"/>'


def arrow(x1: float, y1: float, x2: float, y2: float, cls: str = "wire") -> str:
    return (
        f'<path class="{cls}" d="M{x1} {y1} L{x2} {y2}" marker-end="url(#{cls}-head)"/>'
    )


def elbow(x1: float, y1: float, x2: float, y2: float, cls: str = "wire") -> str:
    """Orthogonal connector (vertical, horizontal, vertical): blueprint style."""
    my = (y1 + y2) / 2
    return f'<path class="{cls}" d="M{x1} {y1} V{my} H{x2} V{y2}" marker-end="url(#{cls}-head)"/>'


def markers(w: int, h: int, points: list[tuple[int, int]]) -> list[str]:
    """Tiny cyan dashes on grid intersections (blueprint markers)."""
    return [
        f'<rect x="{gx * GRID}" y="{gy * GRID}" width="2" height="2" fill="{CYAN}"/>'
        for gx, gy in points
        if gx * GRID < w and gy * GRID < h
    ]


def doc(w: int, h: int, body: list[str], title: str) -> str:
    content = "".join(body)
    used = sorted(set(re.findall(r'href="#i-([\w-]+)"', content)))
    heads = "".join(
        f'<marker id="{c}-head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
        f'markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" '
        f'fill="{col}"/></marker>'
        for c, col in (("wire", WIRE), ("wire-accent", CYAN))
    )
    t = html.escape(title)
    return (
        f'<svg xmlns="{SVG_NS}" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'role="img" aria-label="{t}"><title>{t}</title><style>{STYLE}</style>'
        f"<defs>{heads}{''.join(_symbol(n) for n in used)}</defs>"
        f'<rect width="{w}" height="{h}" fill="{BLACK}"/>{content}</svg>\n'
    )


# ---------------------------------------------------------------- architecture
@dataclass
class Lane:
    title: str
    logo: str
    call: str
    how: str  # how the service reaches this interface
    auto: bool  # registered from the @service with no extra code
    client_label: str
    clients: list[str] = field(default_factory=list)  # product logos, if any
    glyph: str = ""  # plain glyph when the client is not a product


def architecture() -> str:
    w, h = 880, 500
    cols = [150, 440, 730]  # lane centres
    card_w, card_h = 260, 128
    y_client, y_card, y_svc, y_infra = 32, 124, 332, 448
    svc_w, svc_h = 560, 80
    lanes = [
        Lane("CLI", "typer", 'mymcp docs search "oauth"', "thin Typer command", False,
             "your terminal", glyph=">_"),
        Lane("MCP server", "mcp", "tools/call  search_docs", "auto-registered", True,
             "AI clients", clients=["claude", "chatgpt", "goose"]),
        Lane("HTTP API", "fastapi", "POST /api/v1/services/search_docs", "auto-registered", True,
             "apps & scripts", glyph="{ }"),
    ]  # fmt: skip

    b = markers(w, h, [(1, 7), (21, 7), (2, 12), (20, 12)])
    for cx, lane in zip(cols, lanes, strict=True):
        if lane.clients:
            n, s, gap = len(lane.clients), 24, 16
            x0 = cx - (n * s + (n - 1) * gap) / 2
            b += [
                icon(c, x0 + i * (s + gap), y_client, s)
                for i, c in enumerate(lane.clients)
            ]
        else:
            b.append(text(cx, y_client + 19, lane.glyph, 20, "mono muted", weight=700))
        b.append(text(cx, y_client + 44, lane.client_label, 12, "muted"))
        b.append(arrow(cx, y_client + 54, cx, y_card - 4))
        b.append(hackbox(cx - card_w / 2, y_card, card_w, card_h))
        b.append(icon(lane.logo, cx - 14, y_card + 22, 28))
        b.append(text(cx, y_card + 72, lane.title, 17, weight=600))
        b.append(text(cx, y_card + 94, lane.call, 12, "mono muted"))
        b.append(
            text(cx, y_card + 114, lane.how, 12, "accent" if lane.auto else "muted")
        )
        end_x = w / 2 + (cx - w / 2) * 0.35
        cls = "wire-accent" if lane.auto else "wire"
        b.append(elbow(cx, y_card + card_h, end_x, y_svc - 4, cls))

    sx = w / 2 - svc_w / 2
    b.append(hackbox(sx, y_svc, svc_w, svc_h))
    b.append(icon("python", sx + 24, y_svc + svc_h / 2 - 12, 28))
    sig = "@service  search_docs(SearchDocsInput) → SearchDocsResult"
    b.append(text(sx + 72, y_svc + 38, sig, 15, "mono", anchor="start", weight=600))
    b.append(
        text(
            sx + 72,
            y_svc + 62,
            "written once in services/  ·  one Pydantic contract",
            13,
            "accent",
            anchor="start",
        )
    )

    b.append(arrow(w / 2, y_svc + svc_h, w / 2, y_infra - 4))
    b.append(hackbox(sx, y_infra, svc_w, 36, dim=True))
    infra = "models/  ·  common/ config  ·  db/  ·  utils/llm/"
    b.append(text(w / 2, y_infra + 24, infra, 13, "mono muted"))
    return doc(
        w,
        h,
        b,
        "One @service: MCP tool and HTTP route auto-registered, CLI as a thin command",
    )


# -------------------------------------------------------------------- MCP apps
def mcp_apps() -> str:
    canvas_w, canvas_h = 880, 320
    sx, sy, sw, sh = 40, 120, 190, 80  # service box
    rows = [70, 160, 250]  # output row centres
    x_lane, x_out = 300, 520
    label_w = 150  # room for the lane's name before its output arrow
    b = markers(canvas_w, canvas_h, [(1, 1), (21, 7)])
    b.append(hackbox(sx, sy, sw, sh))
    b.append(text(sx + sw / 2, sy + 44, "@service", 16, "mono", weight=600))
    b.append(text(sx + sw / 2, sy + 64, "unchanged", 12, "muted"))

    plain = [("typer", "CLI", "table / json"), ("fastapi", "HTTP API", "JSON")]
    for cy, (logo, name, out) in zip((rows[0], rows[2]), plain, strict=True):
        b.append(elbow(sx + sw, sy + sh / 2, x_lane - 8, cy))
        b.append(icon(logo, x_lane, cy - 12, 24))
        b.append(text(x_lane + 36, cy + 5, name, 15, anchor="start", weight=600))
        b.append(arrow(x_lane + label_w, cy, x_out - 8, cy))
        b.append(text(x_out, cy + 5, out, 13, "mono muted", anchor="start"))

    cy = rows[1]
    b.append(arrow(sx + sw, cy, x_lane - 8, cy, "wire-accent"))
    b.append(icon("mcp", x_lane, cy - 12, 24))
    b.append(
        text(x_lane + 36, cy - 2, "MCP + enhancer", 15, anchor="start", weight=600)
    )
    b.append(
        text(
            x_lane + 36,
            cy + 16,
            "elicit · images · ui://",
            12,
            "accent",
            anchor="start",
        )
    )
    b.append(arrow(x_lane + label_w + 40, cy, x_out - 8, cy, "wire-accent"))

    # chat window (dim) holding the embedded app iframe (primary)
    cw, ch = 330, 160
    cx0, cy0 = x_out, cy - ch / 2
    b.append(hackbox(cx0, cy0, cw, ch, dim=True))
    b.append(icon("claude", cx0 + cw - 30, cy0 + 8, 16))
    fx, fy, fw, fh = cx0 + 16, cy0 + 34, cw - 32, ch - 50
    b.append(hackbox(fx, fy, fw, fh))
    b.append(
        text(
            fx + 14,
            fy + 34,
            "interactive app (iframe)",
            13,
            "accent",
            anchor="start",
            weight=600,
        )
    )
    b += [bar(fx + 14, fy + 50 + i * 14, wd, 6) for i, wd in enumerate((210, 160, 190))]
    return doc(canvas_w, canvas_h, b, "Enhancers add UI on the MCP transport only")


# ----------------------------------------------------------------- add feature
def add_feature() -> str:
    w, h = 880, 260
    step_w, step_h, gap = 236, 88, 50
    y_steps, y_result = 32, 164
    xs = [40 + i * (step_w + gap) for i in range(3)]
    steps = [
        ("1", "models/", "Pydantic input + output", False),
        ("2", "services/", "@service function", False),
        ("3", "src/cli/commands/", "thin CLI command (optional)", True),
    ]
    b = markers(w, h, [(1, 4), (21, 1)])
    for x, (n, folder, what, optional) in zip(xs, steps, strict=True):
        b.append(hackbox(x, y_steps, step_w, step_h, dim=True))
        b.append(
            text(
                x + 24,
                y_steps + 56,
                n,
                22,
                "mono muted" if optional else "mono accent",
                weight=700,
            )
        )
        b.append(
            text(x + 52, y_steps + 48, folder, 15, "mono", anchor="start", weight=600)
        )
        b.append(text(x + 52, y_steps + 70, what, 12, "muted", anchor="start"))
    for a, z in zip(xs, xs[1:], strict=False):
        b.append(
            arrow(a + step_w + 6, y_steps + step_h / 2, z - 8, y_steps + step_h / 2)
        )

    # the @service alone registers the MCP tool and the HTTP route
    rw, rx = step_w, xs[1]
    b.append(
        arrow(rx + rw / 2, y_steps + step_h, rx + rw / 2, y_result - 4, "wire-accent")
    )
    b.append(hackbox(rx, y_result, rw, step_h))
    logos = ("mcp", "fastapi")
    pitch = 56
    x0 = rx + rw / 2 - (len(logos) - 1) * pitch / 2
    b += [
        icon(logo, x0 + i * pitch - 13, y_result + 20, 26)
        for i, logo in enumerate(logos)
    ]
    b.append(
        text(
            rx + rw / 2, y_result + 72, "MCP tool + HTTP route, automatic", 12, "accent"
        )
    )
    return doc(
        w,
        h,
        b,
        "Add a feature: models, a @service (MCP + API automatic), optional CLI command",
    )


def render() -> dict[str, str]:
    return {
        "architecture.svg": architecture(),
        "mcp-apps.svg": mcp_apps(),
        "add-feature.svg": add_feature(),
    }


def main() -> None:
    check = "--check" in sys.argv[1:]
    drift = []
    for name, svg in render().items():
        path = MEDIA / name
        if check:
            if not path.exists() or path.read_text() != svg:
                drift.append(name)
            continue
        path.write_text(svg)
        print(f"wrote media/{name}")
    if drift:
        sys.exit(f"README diagrams out of date: {drift}. Run: make gen_readme_diagrams")


if __name__ == "__main__":
    main()
