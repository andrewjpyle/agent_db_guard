"""Render README graphics: every *.html in SRC_DIR -> a compressed 2x WebP in OUT_DIR.

    uv run --with playwright --with pillow python render.py docs/assets/src docs/assets

Hard failures (exit 1, nothing written for that page):
  - any kit font (Fraunces, Inter, JetBrains Mono) not loaded, so a fallback font never ships
  - an image over the per-image budget after compression
First run on a machine: `uv run --with playwright python -m playwright install chromium`.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

PER_IMAGE_BUDGET = 250 * 1024
FAMILIES = ["Fraunces", "Inter", "JetBrains Mono"]


def compress(png: bytes) -> bytes:
    """WebP, quality 90 first. The kit's film grain defeats PNG (a 1400x760@2x hero is ~700KB as a
    palette PNG); WebP q90 keeps the grain and the text edges clean at ~150-210KB. Steps down only
    if a page would exceed the budget; q80 shows faint blocking on the dark background, so it is
    the floor."""
    from PIL import Image

    im = Image.open(io.BytesIO(png)).convert("RGB")
    data = b""
    for quality in (90, 86, 82, 80):
        buf = io.BytesIO()
        im.save(buf, format="WEBP", quality=quality, method=6)
        data = buf.getvalue()
        if len(data) <= PER_IMAGE_BUDGET:
            break
    return data


def main(src_dir: str, out_dir: str) -> int:
    from playwright.sync_api import sync_playwright

    src, out = Path(src_dir).resolve(), Path(out_dir).resolve()
    pages = sorted(p for p in src.glob("*.html"))
    if not pages:
        print(f"no *.html in {src}", file=sys.stderr)
        return 1
    failed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1400, "height": 760}, device_scale_factor=2)
        for html_path in pages:
            pg = ctx.new_page()
            pg.goto(html_path.as_uri())
            # Force-load every declared face: a page with no Inter text would otherwise never load
            # Inter and look "missing". After this, only a face whose FILE failed stays unloaded.
            pg.evaluate("""async () => { await Promise.allSettled([...document.fonts].map(f => f.load())); await document.fonts.ready; }""")
            pg.wait_for_timeout(300)
            missing = pg.evaluate(
                """(fams) => fams.filter(f => ![...document.fonts].some(ff => ff.family.replace(/['"]/g,'') === f && ff.status === 'loaded'))""",
                FAMILIES)
            if missing:
                print(f"FAIL {html_path.name}: font(s) not loaded: {', '.join(missing)}", file=sys.stderr)
                failed += 1
                pg.close()
                continue
            data = compress(pg.screenshot(full_page=False))
            pg.close()
            if len(data) > PER_IMAGE_BUDGET:
                print(f"FAIL {html_path.name}: {len(data) // 1024}KB > {PER_IMAGE_BUDGET // 1024}KB budget", file=sys.stderr)
                failed += 1
                continue
            target = out / f"{html_path.stem}.webp"
            target.write_bytes(data)
            print(f"ok   {target.name}: {len(data) // 1024}KB")
        browser.close()
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
