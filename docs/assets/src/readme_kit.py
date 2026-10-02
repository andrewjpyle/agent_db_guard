"""README graphics kit: the "Dark Workshop" design system from ai-exec-org-template, made reusable.

A repo's `docs/assets/src/build.py` imports this module, builds each graphic as an HTML page with
the helpers below, and writes them next to itself. `render.py` then turns every HTML page into a
compressed 2x PNG and refuses to write one if a font failed to load.

Rule that makes the graphics trustworthy: anything that shows DATA (a tool response, a report, a
table of numbers) must be built from a committed capture file via `load_capture()`, never typed in
by hand. Structural diagrams (flows, timelines, catalogs of what exists in code) need no capture.

Palette, fonts and components match the gold repo exactly; change them here, not per repo.
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parent
FONT_DIR = KIT_DIR / "fonts"

W, H = 1400, 760  # every graphic is 1400x760 CSS px, rendered at 2x

PALETTE = {
    "bg": "#0D0D0D", "card": "#141312", "line": "#2a2724", "ivory": "#F4EFE6", "muted": "#a39d93",
    "dim": "#6d675f", "amber": "#E8912D", "amber_d": "#C27F21", "good": "#8fbf7a", "bad": "#e0735a",
}


def _font_css() -> str:
    # Relative path: write_pages() copies the fonts next to the HTML, so the committed sources
    # never carry an absolute local path and render the same on any machine.
    css = (FONT_DIR / "fonts.css").read_text(encoding="utf-8")
    return css.replace("FONTDIR", "fonts")


def _base_css() -> str:
    p = PALETTE
    return f"""<style>
{_font_css()}
:root{{--bg:{p['bg']};--card:{p['card']};--line:{p['line']};--ivory:{p['ivory']};--muted:{p['muted']};--dim:{p['dim']};--amber:{p['amber']};--amber-d:{p['amber_d']};--good:{p['good']};--bad:{p['bad']}}}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{background:var(--bg);color:var(--ivory);font-family:Inter,sans-serif;overflow:hidden}}
body{{position:relative}}
.grain{{position:absolute;inset:0;opacity:.06;pointer-events:none;background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3' stitchTiles='stitch'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>")}}
.mono{{font-family:'JetBrains Mono',monospace}}
*{{font-variant-ligatures:none !important;font-feature-settings:'liga' 0,'calt' 0 !important}}
.serif{{font-family:Fraunces,serif}}
.k{{font:500 13px 'JetBrains Mono';letter-spacing:.2em;color:var(--amber);text-transform:uppercase}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px}}
.pill{{font:500 14px 'JetBrains Mono';letter-spacing:.14em;color:var(--amber);border:1.5px solid var(--amber-d);border-radius:999px;padding:9px 20px;display:inline-block}}
.foot{{position:absolute;bottom:22px;left:48px;right:48px;display:flex;justify-content:space-between;font:500 12px 'JetBrains Mono';letter-spacing:.18em;color:var(--dim)}}
.foot b{{color:var(--amber);font-weight:500}}
</style>"""


def esc(s: str) -> str:
    return html.escape(str(s))


def page(body: str, title: str, footer_left: str, footer_right: str = "/ ANDREWJPYLE.COM",
         w: int = W, h: int = H) -> str:
    """A full 1400x760 graphic. footer_left is e.g. 'AGENT-DB-GUARD · REAL RUN 2026-10-01'."""
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(title)}</title>{_base_css()}"
            f"<style>html,body{{width:{w}px;height:{h}px}}</style></head><body><div class='grain'></div>{body}"
            f"<div class='foot'><span>{esc(footer_left)}</span><b>{esc(footer_right)}</b></div></body></html>")


def heading(kicker: str, title_html: str, sub: str = "", left: int = 56, top: int = 48) -> str:
    """Kicker label + serif title. title_html may contain <em> for the amber italic accent."""
    sub_html = (f"<div style='color:var(--muted);font-size:17px;margin-top:10px;max-width:1000px;line-height:1.45'>{sub}</div>"
                if sub else "")
    return (f"<div style='position:absolute;left:{left}px;top:{top}px'><div class='k'>{esc(kicker)}</div>"
            f"<div class='serif' style='font-size:40px;margin-top:10px'>{title_html}</div>{sub_html}</div>")


def em(text: str) -> str:
    return f"<em style='color:var(--amber)'>{esc(text)}</em>"


# ── Archetype 1: hero ────────────────────────────────────────────────────────────────────────

def hero(kicker: str, title: str, accent: str, lede_html: str, rules: list[tuple[str, str]],
         pill: str, right_html: str, footer_left: str) -> str:
    """Left: kicker, 2-line serif title (second line amber italic), lede, 3 numbered rules, pill.
    Right: any diagram (e.g. wheel() or a card stack)."""
    rules_html = "".join(
        f"<div style='display:flex;gap:16px;margin-top:22px'><div class='mono' style='color:var(--amber);font-size:14px;padding-top:4px'>{i:02d}</div>"
        f"<div><div style='font-size:21px;font-weight:600'>{esc(t)}</div><div style='color:var(--muted);font-size:16px;margin-top:5px;line-height:1.4'>{esc(d)}</div></div></div>"
        for i, (t, d) in enumerate(rules, 1))
    body = f"""
<div style='position:absolute;left:64px;top:64px;width:640px'>
  <div class='k'>{esc(kicker)}</div>
  <h1 class='serif' style='font-weight:500;font-size:60px;line-height:1.04;margin-top:22px'>{esc(title)}<br><em style='font-weight:400;color:var(--amber)'>{esc(accent)}</em></h1>
  <div style='margin-top:18px;font-size:19px;color:var(--muted);line-height:1.45'>{lede_html}</div>
  {rules_html}
  <div class='pill' style='margin-top:30px'>{esc(pill)}</div>
</div>
<div style='position:absolute;right:56px;top:70px;width:600px;height:600px;display:flex;align-items:center;justify-content:center'>{right_html}</div>
"""
    return page(body, "hero", footer_left)


def wheel(nodes: list[str], center_top: str, center_main: str, size: int = 540, node_r: int = 40) -> str:
    """Nodes orbiting a glowing hub (the gold hero's seat wheel)."""
    c = size / 2
    r_nodes = size * .385
    hub = int(size * .16)
    parts = [f"<svg width='{size}' height='{size}' viewBox='0 0 {size} {size}'>",
             "<defs><radialGradient id='g'><stop offset='0' stop-color='#E8912D' stop-opacity='.35'/>"
             "<stop offset='1' stop-color='#E8912D' stop-opacity='0'/></radialGradient></defs>",
             f"<circle cx='{c}' cy='{c}' r='{r_nodes + node_r + 14}' fill='none' stroke='#2a2724' stroke-dasharray='3 6'/>",
             f"<circle cx='{c}' cy='{c}' r='{int(r_nodes * .62)}' fill='none' stroke='#2a2724'/>"]
    for i, label in enumerate(nodes):
        a = -math.pi / 2 + i * 2 * math.pi / len(nodes)
        x, y = c + r_nodes * math.cos(a), c + r_nodes * math.sin(a)
        ix, iy = c + hub * math.cos(a), c + hub * math.sin(a)
        ox, oy = x - node_r * math.cos(a), y - node_r * math.sin(a)
        fs = int(node_r * (.5 if len(label) <= 4 else .34))
        parts.append(f"<line x1='{ix:.1f}' y1='{iy:.1f}' x2='{ox:.1f}' y2='{oy:.1f}' stroke='#8a5a22' stroke-width='1.5'/>")
        parts.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{node_r}' fill='#141312' stroke='#E8912D' stroke-width='1.3'/>"
                     f"<text x='{x:.1f}' y='{y + fs * .35:.1f}' text-anchor='middle' font-family='JetBrains Mono' font-size='{fs}' "
                     f"font-weight='500' fill='#F4EFE6'>{esc(label)}</text>")
    parts.append(f"<circle cx='{c}' cy='{c}' r='{hub + 18}' fill='url(#g)'/>"
                 f"<circle cx='{c}' cy='{c}' r='{hub}' fill='#17120c' stroke='#E8912D' stroke-width='1.6'/>"
                 f"<text x='{c}' y='{c - 8}' text-anchor='middle' font-family='JetBrains Mono' font-size='12' letter-spacing='2' fill='#a39d93'>{esc(center_top)}</text>"
                 f"<text x='{c}' y='{c + 22}' text-anchor='middle' font-family='Fraunces' font-size='{int(hub * .36)}' fill='#F4EFE6'>{esc(center_main)}</text>")
    parts.append("</svg>")
    return "".join(parts)


# ── Archetype 2: how it works (boxes + arrows) ───────────────────────────────────────────────

def box(x: int, y: int, w: int, h: int, title: str, lines: list[str], accent: bool = False) -> str:
    border = "var(--amber)" if accent else "var(--line)"
    bg = "#17120c" if accent else "var(--card)"
    li = "".join(f"<div class='mono' style='font-size:13px;color:var(--muted);margin-top:6px'>{esc(t)}</div>" for t in lines)
    return (f"<div style='position:absolute;left:{x}px;top:{y}px;width:{w}px;height:{h}px;background:{bg};border:1.5px solid {border};"
            f"border-radius:12px;padding:16px 18px'><div class='k' style='font-size:12px'>{esc(title)}</div>{li}</div>")


def arrows(specs: list[tuple]) -> str:
    """specs: (x1, y1, x2, y2[, label[, dashed[, side]]]); side is 'above' or 'right'."""
    out = ["<svg style='position:absolute;inset:0' width='1400' height='760'><defs><marker id='a' markerWidth='10' markerHeight='10' "
           "refX='8' refY='5' orient='auto'><path d='M0,0 L10,5 L0,10 z' fill='#C27F21'/></marker></defs>"]
    for s in specs:
        x1, y1, x2, y2 = s[:4]
        label = s[4] if len(s) > 4 else ""
        dashed = s[5] if len(s) > 5 else False
        side = s[6] if len(s) > 6 else "above"
        dash = "stroke-dasharray='6 6'" if dashed else ""
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        lab = ""
        if label and side == "right":
            lab = f"<text x='{mx + 14}' y='{my + 4}' font-family='JetBrains Mono' font-size='12' fill='#a39d93'>{esc(label)}</text>"
        elif label:
            lab = f"<text x='{mx}' y='{my - 14}' text-anchor='middle' font-family='JetBrains Mono' font-size='12' fill='#a39d93'>{esc(label)}</text>"
        out.append(f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' stroke='#C27F21' stroke-width='2' {dash} marker-end='url(#a)'/>{lab}")
    out.append("</svg>")
    return "".join(out)


def flow(kicker: str, title_html: str, subline: str, boxes_html: str, arrow_specs: list[tuple], footer_left: str) -> str:
    body = (heading(kicker, title_html)
            + arrows(arrow_specs)
            + f"<div class='mono' style='position:absolute;left:56px;top:150px;font-size:13px;color:var(--dim)'>{esc(subline)}</div>"
            + boxes_html)
    return page(body, "how-it-works", footer_left)


# ── Archetype 3: anatomy of REAL output (capture-driven) ─────────────────────────────────────

def load_capture(path: str | Path) -> dict:
    """Load a capture written by capture.py. Refuses empty or unprovenanced captures."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("command", "captured_at", "commit", "output"):
        if not data.get(key):
            raise SystemExit(f"capture {path} is missing '{key}': refusing to render data from it")
    if not str(data["output"]).strip():
        raise SystemExit(f"capture {path} has empty output: refusing to render data from it")
    return data


def anatomy(kicker: str, doc_lines: list[tuple[str, str]], notes: list[tuple[int, str]], footer_left: str,
            doc_width: int = 780) -> str:
    """A real document/response on the left, margin notes on the right.
    doc_lines: (style, html) with style in h1,q,h2,b,i,li,li2,m,code. Build them FROM a capture.
    notes: (top_px, text)."""
    style = {"h1": "font:500 26px Fraunces;margin:4px 0 10px", "q": "color:var(--muted);border-left:3px solid var(--line);padding-left:12px;margin-bottom:14px",
             "h2": "font:600 17px Inter;margin:14px 0 6px;color:var(--ivory)", "b": "font-weight:600;color:var(--amber)",
             "i": "color:var(--muted);font-style:italic;margin-top:4px", "li": "margin:4px 0 0 16px", "li2": "margin:3px 0 0 36px;color:var(--muted)",
             "m": "margin-top:14px;color:var(--dim);font:12px 'JetBrains Mono'",
             "code": "font:15px 'JetBrains Mono';color:var(--muted);white-space:pre;margin:3px 0"}
    doc = "".join(f"<div style=\"font-size:16px;line-height:1.5;{style[k]}\">{t}</div>" for k, t in doc_lines)
    callouts = "".join(
        f"<div style='position:absolute;left:{56 + doc_width + 34}px;top:{y}px;width:{1400 - 56 - doc_width - 34 - 56}px;display:flex;gap:12px;align-items:flex-start'>"
        f"<div style='width:26px;height:2px;background:var(--amber);margin-top:11px;flex:none'></div>"
        f"<div style='font-size:16px;line-height:1.4'>{esc(t)}</div></div>" for y, t in notes)
    body = (f"<div style='position:absolute;left:56px;top:40px'><div class='k'>{esc(kicker)}</div></div>"
            f"<div class='card' style='position:absolute;left:56px;top:80px;width:{doc_width}px;max-height:620px;overflow:hidden;padding:22px 26px'>{doc}</div>"
            f"{callouts}")
    return page(body, "anatomy", footer_left)


# ── Archetype 4: timeline ────────────────────────────────────────────────────────────────────

def timeline(kicker: str, title_html: str, sub: str, steps: list[tuple[str, str, str, bool]], footer_left: str) -> str:
    """steps: (time/label, heading, description, highlighted). 3-6 steps."""
    items = "".join(
        f"<div style='position:relative;width:{int(1288 / max(len(steps), 1)) - 16}px'>"
        f"<div style='width:18px;height:18px;border-radius:50%;background:{'var(--amber)' if hl else '#17120c'};border:2px solid var(--amber);margin:0 auto'></div>"
        f"<div class='mono' style='text-align:center;margin-top:16px;color:var(--amber);font-size:14px;letter-spacing:.1em'>{esc(t)}</div>"
        f"<div style='text-align:center;font-size:20px;font-weight:600;margin-top:8px'>{esc(h)}</div>"
        f"<div style='text-align:center;color:var(--muted);font-size:15px;line-height:1.45;margin-top:8px;padding:0 8px'>{esc(d)}</div></div>"
        for t, h, d, hl in steps)
    body = (heading(kicker, title_html, sub, top=56)
            + "<div style='position:absolute;left:70px;right:70px;top:330px;height:2px;background:linear-gradient(90deg,var(--amber-d),var(--line))'></div>"
            + f"<div style='position:absolute;left:56px;right:56px;top:321px;display:flex;justify-content:space-between'>{items}</div>")
    return page(body, "timeline", footer_left)


# ── Archetype 5: catalog grid ────────────────────────────────────────────────────────────────

def catalog(kicker: str, title_html: str, sub_html: str, cards: list[tuple[str, str, str, str]], footer_left: str,
            cols: int = 4, card_height: int = 130) -> str:
    """cards: (label, description, mono line, dim line). Build from what actually exists in code."""
    cells = "".join(
        f"<div class='card' style='padding:18px 20px;min-height:{card_height}px'><div class='k' style='font-size:12px'>{esc(a)}</div>"
        f"<div style='font-size:{18 if card_height >= 200 else 16}px;margin-top:12px;line-height:1.35'>{esc(b)}</div>"
        f"<div class='mono' style='font-size:13px;color:var(--ivory);margin-top:10px'>{esc(c)}</div>"
        f"<div class='mono' style='font-size:12px;color:var(--dim);margin-top:5px'>{esc(d)}</div></div>"
        for a, b, c, d in cards)
    body = (heading(kicker, title_html)
            + f"<div style='position:absolute;left:56px;top:150px;color:var(--muted);font-size:17px'>{sub_html}</div>"
            + f"<div style='position:absolute;left:56px;right:56px;top:196px;display:grid;grid-template-columns:repeat({cols},1fr);gap:14px'>{cells}</div>")
    return page(body, "catalog", footer_left)


def write_pages(out_dir: str | Path, pages: dict[str, str]) -> None:
    import shutil

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fonts_out = out / "fonts"
    if fonts_out.resolve() != FONT_DIR.resolve():  # already in place when the kit is vendored
        fonts_out.mkdir(exist_ok=True)
        for f in FONT_DIR.iterdir():
            if f.suffix in (".woff2", ".txt", ".css"):
                shutil.copy2(f, fonts_out / f.name)
    for name, doc in pages.items():
        (out / f"{name}.html").write_text(doc, encoding="utf-8")
        print("wrote", out / f"{name}.html")
