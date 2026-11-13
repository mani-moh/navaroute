#!/usr/bin/env python3
"""Build the minimal mirror page: copy links for hosted base64 feeds.

The page is deliberately bare: one copy button per feed with a neutral
label and a few-word description. It names no product, protocol or
purpose, makes no request at runtime, and loads no external resource,
so it can be served from any host, including one inside a filtered
network. All feed content is base64 data, never plain text.

Usage:
  python3 scripts/build_mirror_page.py --feeds-dir DIR --base-url URL --out DIR

--feeds-dir holds the *_base64.txt files; --base-url is the public base
under which those files are served (no trailing slash). Output is
<out>/index.html.
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

# (file stem, label, few-word description). Order is the page order.
# Deliberately neutral wording: no product, protocol or purpose words.
FEEDS = (
    ("tier1_all_base64", "working", "tested, working"),
    ("reachable_base64", "online", "responding now"),
    ("active_base64", "recent", "worked this week"),
    ("top100_base64", "top 100", "fastest 100"),
    ("lite500_base64", "lite", "small list"),
    ("tier1_top100_base64", "working · top 100", "tested, fastest 100"),
    ("tier1_lite500_base64", "working · lite", "tested, small list"),
    ("tier1_reachable_base64", "working · online", "tested, responding now"),
)

CSS = """
*{box-sizing:border-box}body{margin:0;background:#0b1220;color:#e5e7eb;
font-family:system-ui,-apple-system,'Segoe UI',sans-serif;line-height:1.6}
.wrap{max-width:640px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:1.4em;margin:0 0 20px;color:#94a3b8;font-weight:600}
.row{display:flex;align-items:center;gap:12px;background:#111c33;
border:1px solid #1e3a5f;border-radius:12px;padding:12px 16px;margin-bottom:10px}
.row b{font-size:1.05em}.row span{color:#94a3b8;font-size:.9em}
.row .sp{flex:1}
button.copy{background:#0369a1;color:#fff;border:0;border-radius:8px;
padding:8px 14px;cursor:pointer;font-size:14px;white-space:nowrap}
button.copy:hover{background:#0284c7}
"""

JS = """
function copyText(btn,txt){var done=function(){var o=btn.textContent;btn.textContent='\\u2713';
setTimeout(function(){btn.textContent=o},1500)};
if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(txt).then(done,fallback)}
else{fallback()}function fallback(){var t=document.createElement('textarea');t.value=txt;
document.body.appendChild(t);t.select();try{document.execCommand('copy')}catch(e){}document.body.removeChild(t);done()}}
"""


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def build(feeds_dir: Path, base_url: str, out_dir: Path) -> Path:
    base_url = base_url.rstrip("/")
    rows = []
    for stem, label, desc in FEEDS:
        path = feeds_dir / f"{stem}.txt"
        if not path.exists():
            continue
        url = f"{base_url}/{stem}.txt"
        rows.append(
            f'<div class="row"><div><b>{_esc(label)}</b><br>'
            f'<span>{_esc(desc)}</span></div><div class="sp"></div>'
            f'<button class="copy" onclick="copyText(this,\'{url}\')">Copy</button></div>'
        )
    page = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Links</title>
<style>{CSS}</style></head>
<body><div class="wrap"><h1>Links</h1>
{''.join(rows)}
</div>
<script>{JS}</script>
</body></html>
"""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(page, encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the minimal mirror page.")
    parser.add_argument("--feeds-dir", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    out_path = build(args.feeds_dir.resolve(), args.base_url, args.out)
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
