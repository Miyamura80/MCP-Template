"""Server-rendered pages in the Edison "Hackbox" brand (see the landing page's
``landing-page/src/styles/global.css``): void black, Core Cyan for anything
active, Archivo type, square corners, panels drawn as a cyan wireframe box with
a filled tag label.

Used by the dashboard and the OAuth consent pages. No script: the CSP allows
inline styles and the Archivo webfont, nothing else, and forbids framing (the
buttons on these pages approve things).
"""

from __future__ import annotations

import html

from fastapi.responses import HTMLResponse

from common import global_config


def page_csp(form_action: tuple[str, ...] = ()) -> str:
    """The pages' CSP. ``form_action`` adds origins a form may end up at: a
    browser checks a form's redirects against it too, so a form that 303s to
    another site needs that site listed."""
    targets = " ".join(("'self'", *form_action))
    return (
        "default-src 'none'; "
        "style-src 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        f"img-src data:; base-uri 'none'; form-action {targets}; "
        "frame-ancestors 'none'"
    )


_FONT = (
    "https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&display=swap"
)

# Tokens mirror landing-page/src/styles/global.css (@theme).
_CSS = """
:root{--bg:#000;--s1:#0a0a0a;--s2:#1c1c1c;--border:#383838;--fg:#fff;
--muted:#9ba4a6;--subtle:#5e6575;--accent:#c3fffd;--accent-strong:#7de6e2;
--green:#00c781;--red:#ff3b4d;
--sans:"Archivo",ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
--mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box}
html{background:var(--bg);color:var(--fg);font-family:var(--sans);
-webkit-font-smoothing:antialiased;-webkit-text-size-adjust:100%}
body{margin:0;min-height:100vh;line-height:1.55;overflow-x:clip;
background-image:linear-gradient(to right,rgba(195,255,253,.05) 1px,transparent 1px),
linear-gradient(to bottom,rgba(195,255,253,.05) 1px,transparent 1px);
background-size:56px 56px;background-attachment:fixed}
a{color:var(--accent);text-underline-offset:3px}
a:hover{color:var(--fg)}
code,pre,.mono{font-family:var(--mono)}
:not(pre)>code{overflow-wrap:anywhere;font-size:.9em;color:var(--accent)}
.topbar{position:sticky;top:0;z-index:2;border-bottom:1px solid var(--border);
background:rgba(0,0,0,.85);backdrop-filter:blur(8px)}
.topbar-in{max-width:56rem;margin:0 auto;padding:0 1rem;height:3.5rem;display:flex;
align-items:center;gap:1.5rem}
.brand{display:flex;align-items:center;gap:.6rem;font-weight:600;letter-spacing:-.01em;
color:var(--fg);text-decoration:none;white-space:nowrap}
.mark{display:grid;place-items:center;width:1.75rem;height:1.75rem;background:var(--accent);
color:#000;font-family:var(--mono);font-weight:700;font-size:.85rem}
.tabs{display:flex;gap:1.25rem;flex:1;min-width:0;overflow-x:auto}
.tabs a{color:var(--muted);text-decoration:none;font-size:.9rem;padding:1rem 0;
border-bottom:2px solid transparent;white-space:nowrap}
.tabs a:hover{color:var(--fg)}
.tabs a[aria-current=page]{color:var(--fg);border-bottom-color:var(--accent)}
.who{color:var(--subtle);font-family:var(--mono);font-size:.75rem;white-space:nowrap}
main{max-width:56rem;margin:0 auto;padding:3rem 1rem 5rem}
main.narrow{max-width:34rem}
.eyebrow{display:inline-flex;align-items:center;gap:.5rem;border:1px solid rgba(195,255,253,.5);
background:var(--s1);padding:.3rem .8rem;font-family:var(--mono);font-size:.7rem;
letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.eyebrow:before{content:"";width:.4rem;height:.4rem;background:var(--accent)}
h1{font-size:2rem;line-height:1.15;letter-spacing:-.02em;margin:1rem 0 .5rem;font-weight:700}
h1+.box,h1+form{margin-top:2.25rem}
h2{font-size:1.1rem;margin:0 0 1rem;font-weight:600}
.lead{color:var(--muted);margin:0 0 2rem;max-width:40rem}
.box{position:relative;border:1px solid var(--accent);background:var(--s1);
padding:2.25rem 1.5rem 1.5rem;margin:0 0 1.5rem}
.box>.tag{position:absolute;top:-1px;left:-1px;background:var(--accent);color:#000;
font-family:var(--mono);font-size:.7rem;font-weight:700;letter-spacing:.12em;
text-transform:uppercase;padding:.25rem .6rem}
.field{display:block;margin:0 0 1.25rem}
.box>:last-child,.box>form>:last-child,.box .field:last-of-type{margin-bottom:0}
.lead code{white-space:nowrap}
.field>span{display:block;font-size:.8rem;font-family:var(--mono);letter-spacing:.08em;
text-transform:uppercase;color:var(--muted);margin:0 0 .4rem}
input[type=text],input:not([type]),select{width:100%;font:inherit;color:var(--fg);
background:var(--bg);border:1px solid var(--border);border-radius:0;padding:.65rem .75rem;
outline:none;appearance:none}
select{background-image:linear-gradient(45deg,transparent 50%,var(--muted) 50%),
linear-gradient(135deg,var(--muted) 50%,transparent 50%);
background-position:calc(100% - 1.1rem) 55%,calc(100% - .8rem) 55%;
background-size:.3rem .3rem;background-repeat:no-repeat;padding-right:2rem}
input:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.actions{display:flex;flex-wrap:wrap;gap:.75rem;align-items:center;margin-top:1.5rem}
button,.btn{font:inherit;font-weight:600;font-size:.9rem;border-radius:0;cursor:pointer;
min-height:2.75rem;padding:0 1.25rem;display:inline-flex;align-items:center;
text-decoration:none;border:1px solid rgba(195,255,253,.4);background:transparent;color:var(--fg)}
button:hover,.btn:hover{border-color:var(--accent);background:var(--s2);color:var(--fg)}
.allow,.primary{background:var(--accent);color:#000;border-color:var(--accent)}
.allow:hover,.primary:hover{background:var(--accent-strong);color:#000}
.danger{border-color:rgba(255,59,77,.5);color:var(--red);min-height:2.25rem;font-size:.8rem;
padding:0 .8rem}
.danger:hover{border-color:var(--red);background:rgba(255,59,77,.08);color:var(--red)}
.note,.warn{border-left:2px solid var(--accent);background:var(--s2);color:var(--fg);
padding:.75rem 1rem;margin:0 0 1rem;font-size:.92rem}
.warn{border-left-color:var(--red)}
.secret{display:block;font-family:var(--mono);font-size:.95rem;background:var(--bg);
border:1px dashed var(--accent);padding:1rem;color:var(--accent);overflow-wrap:anywhere;
user-select:all;margin:0 0 1rem}
.meta{display:flex;flex-wrap:wrap;gap:.5rem;margin:0 0 1.25rem}
.pill{font-family:var(--mono);font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;
border:1px solid var(--border);padding:.2rem .55rem;color:var(--muted)}
.pill.on{border-color:var(--accent);color:var(--accent)}
.pill.ok{border-color:var(--green);color:var(--green)}
.pill.bad{border-color:var(--red);color:var(--red)}
table{width:100%;border-collapse:collapse;font-size:.92rem}
th{text-align:left;font-family:var(--mono);font-size:.7rem;letter-spacing:.1em;
text-transform:uppercase;color:var(--subtle);font-weight:500;padding:0 .75rem .6rem 0;
border-bottom:1px solid var(--border)}
td{padding:.85rem .75rem .85rem 0;border-bottom:1px solid var(--s2);vertical-align:middle}
td:last-child,th:last-child{text-align:right;padding-right:0}
tr.pick td{background:rgba(195,255,253,.06)}
table.plans td:first-child,table.plans th:first-child{padding-left:.75rem}
table.plans th{white-space:nowrap}
tr.pick td:first-child{box-shadow:inset 2px 0 0 var(--accent)}
.meter{height:.5rem;border:1px solid var(--border);background:var(--bg);margin:0 0 .75rem}
.meter>span{display:block;height:100%;background:var(--accent)}
.empty{color:var(--muted);margin:0}
pre{background:var(--bg);border:1px solid var(--border);padding:1rem;overflow-x:auto;
font-size:.85rem;color:var(--fg);margin:0 0 1rem}
@media (max-width:640px){h1{font-size:1.6rem}.who{display:none}
.box{padding:2.25rem 1rem 1rem}td,th{padding-right:.5rem}}
"""


def render_page(
    title: str,
    body: str,
    status_code: int = 200,
    *,
    header: str = "",
    narrow: bool = True,
    form_action: tuple[str, ...] = (),
) -> HTMLResponse:
    """One branded page. ``header`` replaces the default brand-only top bar."""
    brand = html.escape(global_config.branding.title)
    topbar = header or (
        "<header class='topbar'><div class='topbar-in'>"
        f"<span class='brand'><span class='mark'>{brand[:1]}</span>{brand}</span>"
        "</div></header>"
    )
    content = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta name='color-scheme' content='dark'>"
        f"<title>{html.escape(title)} · {brand}</title>"
        "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
        f"<link rel='stylesheet' href='{_FONT}'>"
        f"<style>{_CSS}</style></head><body>{topbar}"
        f"<main class='{'narrow' if narrow else ''}'>{body}</main></body></html>"
    )
    response = HTMLResponse(content=content, status_code=status_code)
    response.headers["Content-Security-Policy"] = page_csp(form_action)
    response.headers["Cache-Control"] = "no-store"
    return response
