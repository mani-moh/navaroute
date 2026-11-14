#!/usr/bin/env python3
"""Build the public NavaRoute site: one self-contained index.html.

Reads only public repo data (subs/*.txt, sources.json, STATUS.md,
data/tier1_results.json) and renders a bilingual (Persian/English) page
with every subscription feed, client links and how-to guidance. All data
is baked into the HTML at build time: the page makes no runtime request
to GitHub (or anywhere else), so it works from any mirror and loads
where github.com is unreachable.

Usage: python3 scripts/build_public_site.py [--repo-root DIR] [--out DIR]
Output: <out>/index.html
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

REPO = "mani-moh/navaroute"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/master"
MIRROR_BASE = f"https://cdn.jsdelivr.net/gh/{REPO}@master"
REPO_URL = f"https://github.com/{REPO}"
EXPLORER_URL = "https://mani-moh.github.io/navaroute/explorer/"

MAIN_FEEDS = ("all", "reachable", "active", "top100", "lite500", "verified")
MAIN_DESCRIPTIONS = {
    "all": "every accepted, deduplicated public config",
    "reachable": "configs that answered a socket check in the latest run",
    "active": "configs that answered a socket check at least once in the last 7 days",
    "top100": "up to 100 of the reachable pool, fastest socket latency first",
    "lite500": "up to 500 configs sampled across protocol groups",
    "verified": "deprecated mirror of reachable (kept for old clients)",
}
TIER1_PREFIX = "tier1_"

CLIENTS = [
    {
        "name": "v2rayNG",
        "platforms": "Android",
        "url": "https://github.com/2dust/v2rayNG/releases",
        "note": "APK from GitHub releases (not in Google Play)",
    },
    {
        "name": "v2rayN",
        "platforms": "Windows, macOS, Linux",
        "url": "https://github.com/2dust/v2rayN",
        "note": "Desktop client by the same author as v2rayNG",
    },
    {
        "name": "Hiddify",
        "platforms": "Windows, macOS, Linux, Android, iOS",
        "url": "https://github.com/hiddify/hiddify-app",
        "note": "One app on every platform; also on the App Store",
    },
    {
        "name": "Streisand",
        "platforms": "iOS, macOS",
        "url": "https://apps.apple.com/us/app/streisand/id6450534064",
        "note": "Free on the App Store",
    },
    {
        "name": "NekoBox",
        "platforms": "Android",
        "url": "https://github.com/MatsuriDayo/NekoBoxForAndroid/releases",
        "note": "sing-box based, with plugins",
    },
]

# UI strings: English + Persian. Keys are shared; values are (en, fa).
STRINGS = {
    "tagline": (
        "Free public proxy subscriptions, refreshed automatically.",
        "اشتراک‌های عمومی و رایگان پروکسی، به‌صورت خودکار به‌روز می‌شوند.",
    ),
    "updated": ("Last updated", "آخرین به‌روز‌رسانی"),
    "sources": ("Sources", "منابع"),
    "configs": ("Configs", "کانفیگ‌ها"),
    "reachable": ("Socket-reachable", "قابل دسترس (سوکت)"),
    "tier1_passed": ("Tier-1 passed", "موفق در Tier 1"),
    "get_feeds": ("Get the subscriptions", "دریافت اشتراک‌ها"),
    "get_feeds_sub": (
        "Copy a link, paste it into your client as a subscription, and update. "
        "Every feed below is a plain list of proxy configs, one per line.",
        "یک لینک را کپی کنید، آن را در کلاینت خود به‌عنوان اشتراک وارد کنید و به‌روز کنید. "
        "هر فید زیر یک لیست ساده از کانفیگ‌های پروکسی است، یکی در هر خط.",
    ),
    "copy_link": ("Copy link", "کپی لینک"),
    "copy_b64": ("Copy Base64", "کپی Base64"),
    "mirror": ("Mirror", "آینه"),
    "copied": ("Copied!", "کپی شد!"),
    "search_feeds": ("Search feeds…", "جستجوی فیدها…"),
    "all_feeds": ("All feeds", "همه فیدها"),
    "feed": ("Feed", "فید"),
    "count": ("Configs", "کانفیگ‌ها"),
    "tier1": ("Tier-1", "Tier 1"),
    "links": ("Links", "لینک‌ها"),
    "howto": ("How to use", "راهنمای استفاده"),
    "step1": ("1. Install a client", "۱. نصب یک کلاینت"),
    "step1_body": (
        "Pick one of the free clients below for your device. Install it only "
        "from its official page — a tampered client sees all your traffic.",
        "یکی از کلاینت‌های رایگان زیر را برای دستگاه خود انتخاب کنید. آن را فقط "
        "از صفحه رسمی‌اش نصب کنید — یک کلاینت دستکاری‌شده تمام ترافیک شما را می‌بیند.",
    ),
    "step2": ("2. Copy a subscription link", "۲. کپی لینک اشتراک"),
    "step2_body": (
        "Start with the tier1_all feed (configs that passed a real connection "
        "test) or the reachable feed. Use the Base64 link if your client "
        "prefers it.",
        "با فید tier1_all شروع کنید (کانفیگ‌هایی که تست اتصال واقعی را گذرانده‌اند) "
        "یا فید reachable. اگر کلاینت شما Base64 را ترجیح می‌دهد، از لینک Base64 استفاده کنید.",
    ),
    "step3": ("3. Add it and update", "۳. اضافه کردن و به‌روز‌رسانی"),
    "step3_body": (
        "In your client: add a subscription, paste the link, update, then pick "
        "a config and connect. Test a few — public configs come and go, and "
        "many will be slow or dead. Update the subscription regularly.",
        "در کلاینت: یک اشتراک اضافه کنید، لینک را بچسبانید، به‌روز کنید، سپس یک "
        "کانفیگ را انتخاب و وصل شوید. چند مورد را امتحان کنید — کانفیگ‌های عمومی "
        "می‌آیند و می‌روند و بسیاری کند یا از کار افتاده‌اند. اشتراک را مرتب به‌روز کنید.",
    ),
    "clients": ("Compatible clients", "کلاینت‌های سازگار"),
    "clients_sub": (
        "Free, open-source apps that understand these subscription links. "
        "Also in the App Store: Shadowrocket (paid), V2Box and FoXray (free).",
        "برنامه‌های رایگان و متن‌باز که این لینک‌های اشتراک را می‌فهمند. "
        "همچنین در اپ استور: Shadowrocket (پولی)، V2Box و FoXray (رایگان).",
    ),
    "meanings": ("What the labels mean", "معنی برچسب‌ها چیست؟"),
    "meanings_all": (
        "<b>all</b> — every accepted config. Acceptance only means the config "
        "was well-formed and deduplicated; it is not a claim that it works.",
        "<b>all</b> — همه کانفیگ‌های پذیرفته‌شده. پذیرش فقط یعنی کانفیگ سالم و بدون تکرار است؛ "
        "به معنی کار کردن آن نیست.",
    ),
    "meanings_reachable": (
        "<b>reachable</b> — configs that answered a TCP socket check in the "
        "latest run. A socket answer is not an end-to-end working proxy.",
        "<b>reachable</b> — کانفیگ‌هایی که در آخرین بررسی به تست سوکت TCP جواب دادند. "
        "جواب سوکت به معنی کار کردن کامل پروکسی نیست.",
    ),
    "meanings_active": (
        "<b>active</b> — configs that answered a socket check at least once "
        "in the last 7 days.",
        "<b>active</b> — کانفیگ‌هایی که حداقل یک بار در ۷ روز گذشته به تست سوکت جواب داده‌اند.",
    ),
    "meanings_tier1": (
        "<b>tier1_*</b> — configs that passed a real end-to-end connection "
        "test (PattN Realping) on GitHub runners, re-tested every 2 hours. "
        "These are the best first try.",
        "<b>tier1_*</b> — کانفیگ‌هایی که تست اتصال واقعی (PattN Realping) را در GitHub "
        "گذرانده‌اند و هر ۲ ساعت دوباره تست می‌شوند. این‌ها بهترین نقطه شروع هستند.",
    ),
    "meanings_iran": (
        "<b>iran_*</b> — configs that passed the same end-to-end test from "
        "inside Iran (owner's PC). Appears once the owner runs the tier-2 test.",
        "<b>iran_*</b> — کانفیگ‌هایی که همان تست را از داخل ایران (رایانه مالک) گذرانده‌اند. "
        "پس از اجرای تست tier-2 توسط مالک ظاهر می‌شود.",
    ),
    "meanings_b64": (
        "<b>_base64</b> — the same feed with the config rows Base64-encoded. "
        "Some clients only accept this form.",
        "<b>_base64</b> — همان فید با ردیف‌های کانفیگ کدشده به Base64. "
        "برخی کلاینت‌ها فقط این شکل را می‌پذیرند.",
    ),
    "faq": ("Questions", "پرسش‌های متداول"),
    "faq_free_q": ("Is it free?", "آیا رایگان است؟"),
    "faq_free_a": (
        "Yes. The feeds are public and free, and the clients above are free. "
        "Nobody here sells anything.",
        "بله. فیدها عمومی و رایگان هستند و کلاینت‌های بالا هم رایگان‌اند. "
        "اینجا کسی چیزی نمی‌فروشد.",
    ),
    "faq_often_q": ("How often do the feeds update?", "فیدها هر چند وقت به‌روز می‌شوند؟"),
    "faq_often_a": (
        "The main feeds refresh every 6 hours from 181 sources; the tier-1 "
        "feeds re-test configs every 2 hours. This page rebuilds itself on "
        "every update.",
        "فیدهای اصلی هر ۶ ساعت از ۱۸۱ منبع به‌روز می‌شوند؛ فیدهای tier-1 هر ۲ ساعت "
        "دوباره تست می‌شوند. این صفحه با هر به‌روز‌رسانی خودش را بازسازی می‌کند.",
    ),
    "faq_why_q": ("Why do most configs fail?", "چرا بیشتر کانفیگ‌ها کار نمی‌کنند؟"),
    "faq_why_a": (
        "These are public configs shared on the internet. Servers go offline, "
        "get overloaded or get blocked. Try the tier1_all feed first, test "
        "several configs, and update the subscription often.",
        "این‌ها کانفیگ‌های عمومی منتشرشده در اینترنت هستند. سرورها خاموش می‌شوند، "
        "شلوغ می‌شوند یا مسدود می‌شوند. اول فید tier1_all را امتحان کنید، چند "
        "کانفیگ را تست کنید و اشتراک را مرتب به‌روز کنید.",
    ),
    "faq_fetch_q": ("My client can't fetch the link. Why?", "کلاینت من نمی‌تواند لینک را بگیرد. چرا؟"),
    "faq_fetch_a": (
        "The subscription links point at GitHub, which is filtered in Iran. "
        "You need some working connection first to fetch them — or use the "
        "mirror link next to each feed, which serves the same file from a CDN.",
        "لینک‌های اشتراک به GitHub اشاره می‌کنند که در ایران فیلتر است. برای گرفتن "
        "آن‌ها اول به یک اتصال کارا نیاز دارید — یا از لینک آینه کنار هر فید استفاده "
        "کنید که همان فایل را از یک CDN می‌دهد.",
    ),
    "faq_privacy_q": ("Do I need an account? Is anything tracked?", "آیا نیاز به حساب کاربری است؟ چیزی ردیابی می‌شود؟"),
    "faq_privacy_a": (
        "No account, no sign-up, no tracking on this page. The feeds are "
        "static files; your client fetches them directly.",
        "نه حساب کاربری، نه ثبت‌نام، نه ردیابی در این صفحه. فیدها فایل‌های ثابت هستند؛ "
        "کلاینت شما مستقیم آن‌ها را می‌گیرد.",
    ),
    "footer": (
        "NavaRoute is an open-source aggregator of public proxy configs. "
        "It does not run servers, sell access, or promise that any config works.",
        "NavaRoute یک جمع‌آوری‌کننده متن‌باز کانفیگ‌های عمومی پروکسی است. "
        "سرور اجرا نمی‌کند، دسترسی نمی‌فروشد و کار کردن هیچ کانفیگی را تضمین نمی‌کند.",
    ),
    "repo_link": ("GitHub repository", "مخزن GitHub"),
    "explorer_link": ("Pipeline Explorer", "کاوشگر پایپ‌لاین"),
    "issues_link": ("Report a problem", "گزارش مشکل"),
}


def count_uris(path: Path) -> int:
    """Count proxy URI rows in a plain feed file (skip comments/blank)."""
    count = 0
    with path.open(encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#"):
                count += 1
    return count


def collect_feeds(repo_root: Path) -> list[dict]:
    """Inventory subs/*.txt (plain feeds; _base64 twins ride along)."""
    subs = repo_root / "subs"
    feeds: list[dict] = []
    for path in sorted(subs.rglob("*.txt")):
        if path.name.endswith("_base64.txt"):
            continue
        rel = path.relative_to(subs).as_posix()
        stem = path.stem
        group = "main"
        if rel.startswith("tier1_") or rel.startswith("iran_"):
            group = "tier"
        elif "/" in rel:
            group = rel.split("/")[0]  # categories / countries / protocols
        twin = path.with_name(f"{stem}_base64.txt")
        feeds.append(
            {
                "rel": rel,
                "name": stem,
                "group": group,
                "count": count_uris(path),
                "url": f"{RAW_BASE}/subs/{rel}",
                "b64_url": f"{RAW_BASE}/subs/{twin.relative_to(subs).as_posix()}"
                if twin.exists()
                else None,
                "mirror_url": f"{MIRROR_BASE}/subs/{rel}",
            }
        )
    return feeds


def parse_snapshot(repo_root: Path) -> dict:
    """Accepted/reachable counts + snapshot time from STATUS.md (README fallback)."""
    text = ""
    for name in ("STATUS.md", "README.md"):
        candidate = repo_root / name
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            break
    accepted = re.search(r"Accepted Public Nodes\s*\|\s*([\d,]+)", text)
    reachable = re.search(r"Socket Reachable Nodes\s*\|\s*([\d,]+)", text)
    if not accepted:
        accepted = re.search(r"Accepted public nodes:\s*([\d,]+)", text)
    if not reachable:
        reachable = re.search(r"socket reachable:\s*([\d,]+)", text)
    snapshot = re.search(r"Snapshot time:\s*(\S+)", text)
    return {
        "accepted": int(accepted.group(1).replace(",", "")) if accepted else 0,
        "reachable": int(reachable.group(1).replace(",", "")) if reachable else 0,
        "snapshot_time": snapshot.group(1) if snapshot else "",
    }


def load_tier1(repo_root: Path) -> dict:
    """Per-feed {tested, passed} from data/tier1_results.json, if present."""
    path = repo_root / "data" / "tier1_results.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    feeds = data.get("feeds", {}) if isinstance(data, dict) else {}
    return {
        name: {"tested": int(v.get("tested", 0)), "passed": int(v.get("passed", 0))}
        for name, v in feeds.items()
        if isinstance(v, dict)
    }


def load_sources_count(repo_root: Path) -> int:
    path = repo_root / "sources.json"
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    if isinstance(data, dict):
        sources = data.get("sources", [])
        return len(sources) if isinstance(sources, list) else 0
    return len(data) if isinstance(data, list) else 0

CSS = """
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,'Segoe UI',Tahoma,sans-serif;
background:#0b1220;color:#e5e7eb;line-height:1.6}body[dir=rtl]{font-family:system-ui,-apple-system,'Segoe UI',Tahoma,'Vazirmatn',sans-serif}
a{color:#7dd3fc}.wrap{max-width:1020px;margin:0 auto;padding:0 16px 64px}
header{padding:36px 0 8px;text-align:center}header h1{font-size:2.2em;margin:0}
header .tag{color:#94a3b8;margin:6px 0 0}
.lang{position:fixed;top:12px;inset-inline-end:12px;z-index:10}
.lang button{background:#1e293b;color:#e5e7eb;border:1px solid #475569;border-radius:999px;
padding:6px 14px;cursor:pointer;font-size:14px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:24px 0}
.stat{background:#111c33;border:1px solid #1e3a5f;border-radius:12px;padding:14px;text-align:center}
.stat b{display:block;font-size:1.6em;color:#7dd3fc}.stat span{color:#94a3b8;font-size:.9em}
.updated{color:#94a3b8;text-align:center;font-size:.9em}
h2{margin:40px 0 8px;border-bottom:1px solid #1e3a5f;padding-bottom:6px}
.sub{color:#94a3b8;margin:0 0 16px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.card{background:#111c33;border:1px solid #1e3a5f;border-radius:12px;padding:16px}
.card h3{margin:0 0 4px;font-size:1.05em}.card h3 code{color:#7dd3fc}
.card p{margin:0 0 12px;color:#94a3b8;font-size:.92em}
.btnrow{display:flex;flex-wrap:wrap;gap:8px}
button.copy{background:#0369a1;color:#fff;border:0;border-radius:8px;padding:7px 12px;cursor:pointer;font-size:14px}
button.copy:hover{background:#0284c7}
a.mirror{color:#94a3b8;font-size:.85em;align-self:center}
.badge{display:inline-block;background:#14532d;color:#bbf7d0;border-radius:999px;padding:1px 9px;font-size:.8em;margin-inline-start:6px}
.badge.dim{background:#334155;color:#cbd5e1}
.steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.step{background:#111c33;border:1px solid #1e3a5f;border-radius:12px;padding:16px}
.step h3{margin:0 0 6px}.step p{margin:0;color:#cbd5e1;font-size:.95em}
.search{width:100%;padding:10px 12px;border-radius:10px;border:1px solid #475569;background:#0f172a;
color:#e5e7eb;font-size:15px;margin-bottom:12px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}
.chip{background:#1e293b;border:1px solid #475569;color:#e5e7eb;border-radius:999px;padding:5px 13px;
cursor:pointer;font-size:14px}.chip.on{background:#0369a1;border-color:#0369a1}
table{width:100%;border-collapse:collapse;font-size:.92em}
th,td{padding:8px 6px;border-bottom:1px solid #1e293b;text-align:start}
th{color:#94a3b8}td code{font-size:.9em;color:#bae6fd;word-break:break-all}
tr.hidden{display:none}
details{background:#111c33;border:1px solid #1e3a5f;border-radius:12px;padding:12px 16px;margin-bottom:10px}
details summary{cursor:pointer;font-weight:600}
details p{margin:8px 0 0;color:#cbd5e1}
footer{margin-top:48px;border-top:1px solid #1e3a5f;padding-top:16px;color:#94a3b8;font-size:.9em;text-align:center}
.i18n-fa{display:none}body[dir=rtl] .i18n-en{display:none}body[dir=rtl] .i18n-fa{display:inline}
body[dir=rtl] h1,body[dir=rtl] h2,body[dir=rtl] h3,body[dir=rtl] p,body[dir=rtl] summary,body[dir=rtl] td,body[dir=rtl] th{display:block}
body[dir=rtl] td,body[dir=rtl] th{display:table-cell}
"""

JS = """
function setLang(l){document.body.dir=(l==='fa')?'rtl':'ltr';document.documentElement.lang=l;
document.getElementById('langBtn').textContent=(l==='fa')?'English | فارسی':'فارسی | English';
try{localStorage.setItem('navaroute-lang',l)}catch(e){}}
function copyText(btn,txt){var done=function(){var o=btn.textContent;var lang=document.body.dir==='rtl'?'fa':'en';btn.textContent=(lang==='fa'?btn.dataset.doneFa:btn.dataset.doneEn)||btn.dataset.done||o;
setTimeout(function(){btn.textContent=o},1500)};
if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(txt).then(done,fallback)}
else{fallback()}function fallback(){var t=document.createElement('textarea');t.value=txt;
document.body.appendChild(t);t.select();try{document.execCommand('copy')}catch(e){}document.body.removeChild(t);done()}}
var group='all';
function setGroup(g,el){group=g;document.querySelectorAll('.chip').forEach(function(c){c.classList.remove('on')});
el.classList.add('on');applyFilter()}
function applyFilter(){var q=document.getElementById('q').value.toLowerCase();
document.querySelectorAll('#feedTable tbody tr').forEach(function(tr){
var okG=(group==='all'||tr.dataset.group===group);
var okQ=!q||tr.dataset.name.toLowerCase().indexOf(q)>=0;
tr.classList.toggle('hidden',!(okG&&okQ))})}
document.addEventListener('DOMContentLoaded',function(){
var saved=null;try{saved=localStorage.getItem('navaroute-lang')}catch(e){}
setLang(saved||'fa')});
"""


def _t(key: str) -> str:
    en, fa = STRINGS[key]
    return (
        f'<span class="i18n-en">{en}</span><span class="i18n-fa">{fa}</span>'
    )


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _done_attrs(key: str = "copied") -> str:
    """Plain-text done-label attributes for copy buttons.

    _t() returns HTML spans, which must never go inside an attribute
    value: the inner double quotes terminate data-done early and corrupt
    the button markup (that broke every copy button on the public site).
    Keep the two languages as separate plain-text attributes instead;
    copyText() picks the one matching the page direction.
    """
    en, fa = STRINGS[key]
    return f'data-done-en="{_esc(en)}" data-done-fa="{_esc(fa)}"'


def render_html(feeds: list[dict], snapshot: dict, sources: int, tier1: dict) -> str:
    tier1_passed = sum(v["passed"] for v in tier1.values())
    main = [f for f in feeds if f["group"] == "main"]
    others = [f for f in feeds if f["group"] not in ("main", "tier")]

    def tier1_badge(feed: dict) -> str:
        info = tier1.get(feed["name"])
        if not info:
            return ""
        return (
            f'<span class="badge">T1 ✓ {info["passed"]}/{info["tested"]}</span>'
            if info["passed"]
            else ""
        )

    def feed_card(feed: dict) -> str:
        desc = MAIN_DESCRIPTIONS.get(feed["name"], "")
        deprecated = (
            '<span class="badge dim">deprecated</span>' if feed["name"] == "verified" else ""
        )
        b64 = (
            f'<button class="copy" {_done_attrs()} '
            f'onclick="copyText(this,\'{feed["b64_url"]}\')">{_t("copy_b64")}</button>'
            if feed["b64_url"]
            else ""
        )
        return f"""<div class="card"><h3><code>{_esc(feed["name"])}.txt</code>{tier1_badge(feed)}{deprecated}</h3>
<p>{_esc(desc)} — {feed["count"]:,} configs.</p>
<div class="btnrow"><button class="copy" {_done_attrs()} onclick="copyText(this,'{feed["url"]}')"
>{_t("copy_link")}</button>{b64}<a class="mirror" href="{feed["mirror_url"]}">{_t("mirror")}</a></div></div>"""

    def feed_row(feed: dict) -> str:
        info = tier1.get(feed["name"])
        t1 = f'{info["passed"]}/{info["tested"]}' if info else "—"
        b64 = (
            f'<button class="copy" {_done_attrs()} onclick="copyText(this,\'{feed["b64_url"]}\')">b64</button>'
            if feed["b64_url"]
            else ""
        )
        return f"""<tr data-group="{feed["group"]}" data-name="{_esc(feed["name"])}">
<td><code>{_esc(feed["rel"])}</code></td><td>{feed["count"]:,}</td><td>{t1}</td>
<td><button class="copy" {_done_attrs()} onclick="copyText(this,'{feed["url"]}')"
>{_t("copy_link")}</button> {b64}</td></tr>"""

    groups = sorted({f["group"] for f in others})
    chips = "".join(
        f'<button class="chip" onclick="setGroup(\'{g}\',this)">{_esc(g)}</button>'
        for g in groups
    )
    client_cards = "".join(
        f"""<div class="card"><h3>{_esc(c["name"])}</h3><p>{_esc(c["platforms"])} — {_esc(c["note"])}</p>
<div class="btnrow"><a href="{c["url"]}" target="_blank" rel="noopener"><button class="copy">"""
        f"""{_esc(c["name"])} ↗</button></a></div></div>"""
        for c in CLIENTS
    )
    snapshot_line = snapshot["snapshot_time"] or "—"
    return f"""<!doctype html>
<html lang="fa" dir="rtl">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NavaRoute — {_esc("Free proxy subscriptions")}</title>
<meta name="description" content="NavaRoute: free public proxy subscription feeds, clients and how-to.">
<style>{CSS}</style></head>
<body dir="rtl">
<div class="lang"><button id="langBtn" onclick="setLang(document.body.dir==='rtl'?'en':'fa')">English | فارسی</button></div>
<div class="wrap">
<header><h1>NavaRoute</h1><p class="tag">{_t("tagline")}</p></header>
<div class="stats">
<div class="stat"><b>{sources:,}</b><span>{_t("sources")}</span></div>
<div class="stat"><b>{snapshot["accepted"]:,}</b><span>{_t("configs")}</span></div>
<div class="stat"><b>{snapshot["reachable"]:,}</b><span>{_t("reachable")}</span></div>
<div class="stat"><b>{tier1_passed:,}</b><span>{_t("tier1_passed")}</span></div>
</div>
<p class="updated">{_t("updated")}: {snapshot_line}</p>

<h2>{_t("get_feeds")}</h2>
<p class="sub">{_t("get_feeds_sub")}</p>
<div class="cards">{''.join(feed_card(f) for f in main)}</div>

<h2>{_t("howto")}</h2>
<div class="steps">
<div class="step"><h3>{_t("step1")}</h3><p>{_t("step1_body")}</p></div>
<div class="step"><h3>{_t("step2")}</h3><p>{_t("step2_body")}</p></div>
<div class="step"><h3>{_t("step3")}</h3><p>{_t("step3_body")}</p></div>
</div>

<h2>{_t("clients")}</h2>
<p class="sub">{_t("clients_sub")}</p>
<div class="cards">{client_cards}</div>

<h2>{_t("meanings")}</h2>
<p>{_t("meanings_all")}</p>
<p>{_t("meanings_reachable")}</p>
<p>{_t("meanings_active")}</p>
<p>{_t("meanings_tier1")}</p>
<p>{_t("meanings_iran")}</p>
<p>{_t("meanings_b64")}</p>

<h2>{_t("all_feeds")}</h2>
<input id="q" class="search" placeholder="{_esc("Search feeds… / جستجوی فیدها…")}" oninput="applyFilter()">
<div class="chips"><button class="chip on" onclick="setGroup('all',this)">all</button>
<button class="chip" onclick="setGroup('tier',this)">tier</button>{chips}</div>
<table id="feedTable"><thead><tr><th>{_t("feed")}</th><th>{_t("count")}</th><th>{_t("tier1")}</th><th>{_t("links")}</th></tr></thead>
<tbody>{''.join(feed_row(f) for f in feeds)}</tbody></table>

<h2>{_t("faq")}</h2>
<details><summary>{_t("faq_free_q")}</summary><p>{_t("faq_free_a")}</p></details>
<details><summary>{_t("faq_often_q")}</summary><p>{_t("faq_often_a")}</p></details>
<details><summary>{_t("faq_why_q")}</summary><p>{_t("faq_why_a")}</p></details>
<details><summary>{_t("faq_fetch_q")}</summary><p>{_t("faq_fetch_a")}</p></details>
<details><summary>{_t("faq_privacy_q")}</summary><p>{_t("faq_privacy_a")}</p></details>

<footer><p>{_t("footer")}</p>
<p><a href="{REPO_URL}">{_t("repo_link")}</a> · <a href="{EXPLORER_URL}">{_t("explorer_link")}</a> ·
<a href="{REPO_URL}/issues">{_t("issues_link")}</a></p></footer>
</div>
<script>{JS}</script>
</body></html>
"""


def build(repo_root: Path, out_dir: Path) -> Path:
    feeds = collect_feeds(repo_root)
    snapshot = parse_snapshot(repo_root)
    sources = load_sources_count(repo_root)
    tier1 = load_tier1(repo_root)
    for feed in feeds:
        base = feed["name"]
        if feed["group"] == "tier" and base.startswith("tier1_"):
            feed["tier1"] = tier1.get(base[len("tier1_"):])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(
        render_html(feeds, snapshot, sources, tier1), encoding="utf-8"
    )
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the public NavaRoute site.")
    parser.add_argument("--repo-root", default=".", type=Path)
    parser.add_argument("--out", default="site", type=Path)
    args = parser.parse_args(argv)
    out_path = build(args.repo_root.resolve(), args.out)
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
