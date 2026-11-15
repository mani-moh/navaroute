#!/usr/bin/env python3
"""Build the NavaRoute Pipeline Explorer: one self-contained index.html.

Replaces the old React/Vite built bundle (whose source lived off-repo on
a Windows PC and went stale) with a repo-native page generated from the
repo itself: the pipeline stages, the automation, the repo anatomy, the
published feeds (with live counts) and the trust boundaries. All data is
baked into the HTML at build time: the page makes no runtime request to
GitHub (or anywhere else), so it works from any mirror and loads where
github.com is unreachable. Bilingual (Persian default, English toggle),
same pattern as scripts/build_public_site.py.

Usage: python3 scripts/build_explorer.py [--repo-root DIR] [--out DIR]
Output: <out>/index.html
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_public_site as bps  # noqa: E402
from src.tier1 import is_tested_variant, variant_group  # noqa: E402

REPO = "mani-moh/navaroute"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/master"
REPO_URL = f"https://github.com/{REPO}"
SITE_URL = "https://mani-moh.github.io/navaroute/"

# Core feeds, in "start here" order (same as the public site).
MAIN_FEEDS = ("tested", "tested-iran", "online", "online-week",
              "fastest-100", "balanced-500", "all")

# Meanings for the core feeds (en, fa). Longer than the public site's
# card blurbs: the explorer explains what each feed is and is not.
CORE_MEANINGS = {
    "all": (
        "Every accepted public connection after identity validation and "
        "dedup. Acceptance is not a working-credential claim.",
        "همه اتصال‌های عمومی پذیرفته‌شده پس از اعتبارسنجی هویت و حذف تکرار. "
        "پذیرش به معنی کار کردن کانفیگ نیست.",
    ),
    "online": (
        "Successful dated socket observations from the emitting "
        "generation. Empty if checks did not run or none succeeded.",
        "مشاهده‌های سوکت تاریخ‌دار موفق از نسل منتشرشده. اگر بررسی‌ها اجرا "
        "نشده باشند یا هیچ‌کدام موفق نباشند، خالی است.",
    ),
    "online-week": (
        "Configs with a successful socket observation in the last 7 days, "
        "from the committed reachability history. Socket observation only, "
        "not end-to-end or Iran-tested.",
        "کانفیگ‌هایی با مشاهده سوکت موفق در ۷ روز گذشته، از تاریخچه ثبت‌شده. "
        "فقط مشاهده سوکت است، نه تست سرتاسری و نه تست‌شده از ایران.",
    ),
    "fastest-100": (
        "Up to 100 from the online pool, measured connection latency "
        "first; not throughput or streaming performance.",
        "تا ۱۰۰ مورد از استخر online، بر اساس کمترین تأخیر اتصال؛ نه توان "
        "عملیاتی و نه عملکرد استریم.",
    ),
    "balanced-500": (
        "Up to 500 connections, sampled across protocol buckets.",
        "تا ۵۰۰ اتصال، نمونه‌برداری‌شده در گروه‌های پروتکل.",
    ),
    "tested": (
        "Configs that passed a full-connection Realping test on GitHub "
        "runners. The global tested feed.",
        "کانفیگ‌هایی که تست اتصال کامل Realping را در GitHub گذرانده‌اند. "
        "فید تست‌شده سراسری.",
    ),
    "tested-iran": (
        "Configs that passed the same Realping test from inside Iran "
        "(owner PC). Appears once the owner runs the tier-2 test.",
        "کانفیگ‌هایی که همان تست Realping را از داخل ایران (رایانه مالک) "
        "گذرانده‌اند. پس از اجرای تست tier-2 توسط مالک ظاهر می‌شود.",
    ),
}

# Meanings for known category slices (en, fa). Unknown categories fall
# back to CATEGORY_GENERIC.
CATEGORY_MEANINGS = {
    "navaroute-special": (
        "Primary special feed: supplied parameters matching the "
        "special-path policy. Not a bypass claim, no manufactured variants.",
        "فید ویژه اصلی: پارامترهای ارائه‌شده مطابق سیاست مسیر ویژه. ادعای "
        "دور زدن نیست و گونه‌ای ساخته نشده است.",
    ),
    "pattn-special": (
        "Legacy raw-path alias of the NavaRoute special feed (same rows, "
        "same headers policy).",
        "نام مستعار قدیمی مسیر خام فید ویژه NavaRoute (همان ردیف‌ها، همان "
        "سیاست سربرگ).",
    ),
    "cdn-ws-grpc": (
        "CDN/WebSocket/gRPC transport settings as supplied or claimed "
        "upstream.",
        "تنظیمات حمل CDN/WebSocket/gRPC مطابق آنچه ارائه یا ادعا شده است.",
    ),
    "reality": (
        "REALITY-fingerprint settings supplied by the source.",
        "تنظیمات اثر انگشت REALITY ارائه‌شده توسط منبع.",
    ),
    "iran-optimized": (
        "Legacy filename for Iran-related upstream claims; not optimized "
        "routing, not Iranian-ISP evidence.",
        "نام فایل قدیمی برای ادعاهای مرتبط با ایران؛ نه مسیریابی بهینه‌شده و "
        "نه مدرک تست از ISP ایران.",
    ),
    "confighub": (
        "ConfigsHUB-labelled nodes; source identity claim, functionality "
        "not validated.",
        "گره‌های برچسب‌خورده ConfigsHUB؛ ادعای هویت منبع است و عملکرد آن "
        "اعتبارسنجی نشده است.",
    ),
    "whitelist": (
        "Whitelist-labelled nodes (source-supplied claim; global "
        "connectivity not established).",
        "گره‌های برچسب‌خورده whitelist (ادعای منبع؛ اتصال سراسری اثبات نشده است).",
    ),
}
CATEGORY_GENERIC = (
    "Category selection from supplied parameters or upstream claims.",
    "گزینش دسته‌بندی از پارامترهای ارائه‌شده یا ادعاهای منبع.",
)

# Protocol schemes whose transport is UDP-style (excluded from socket
# probing). All other protocol slices are probed like the core feeds.
UDP_SCHEMES = {"hysteria2", "tuic", "wireguard"}


def slice_meaning(rel: str, name: str) -> tuple[str, str]:
    """Meaning (en, fa) for a slice feed, by folder and stem.

    ``name`` is the file stem without the .txt extension (as produced by
    build_public_site.collect_feeds); ``rel`` only supplies the folder.
    """
    folder = rel.split("/")[0] if "/" in rel else ""
    base = variant_group(name) or name
    tested_suffix = ""
    if name.endswith("-tested-iran"):
        tested_suffix = "iran"
    elif name.endswith("-tested"):
        tested_suffix = "t1"
    if folder == "protocols":
        scheme = base.upper()
        en = f"{scheme}-scheme nodes; supplied parameters must be supported by the consumer."
        fa = f"گره‌های پروتکل {scheme}؛ پارامترهای ارائه‌شده باید توسط کلاینت پشتیبانی شوند."
        if base in UDP_SCHEMES:
            en += " Excluded from socket probing (UDP-style transport)."
            fa += " از تست سوکت حذف شده‌اند (حمل UDP)."
    elif folder == "countries":
        en = (f"Upstream remarks/tags claiming {base.upper()} exit; "
              "not geolocation.")
        fa = (f"برچسب‌ها/توضیحات منبع که خروج از {base.upper()} را ادعا "
              "می‌کنند؛ موقعیت جغرافیایی نیست.")
    elif folder == "categories":
        en, fa = CATEGORY_MEANINGS.get(base, CATEGORY_GENERIC)
    else:
        en, fa = CORE_MEANINGS.get(base, ("", ""))
    if tested_suffix == "t1":
        en += " This variant keeps only the configs that passed tier-1 Realping on GitHub, plus the GitHub-blocked set, sorted by delay."
        fa += " این گونه فقط کانفیگ‌هایی را نگه می‌دارد که Realping tier-1 را در GitHub گذرانده‌اند، به‌علاوه مجموعه مسدودشده در GitHub، مرتب‌شده بر اساس تأخیر."
    elif tested_suffix == "iran":
        en += " This variant keeps only the configs that passed Realping from inside Iran, sorted by Iran delay."
        fa += " این گونه فقط کانفیگ‌هایی را نگه می‌دارد که Realping را از داخل ایران گذرانده‌اند، مرتب‌شده بر اساس تأخیر ایران."
    return en, fa


def feed_kind(rel: str, name: str) -> tuple[str, str]:
    """(kind, trust) for a feed: kind is the table filter, trust is the label.

    ``name`` is the file stem without the .txt extension.
    """
    folder = rel.split("/")[0] if "/" in rel else ""
    if name.endswith("-tested-iran"):
        return "tested-iran", "end-to-end (Iran)"
    if is_tested_variant(name):
        return "tested", "end-to-end (GitHub)"
    if folder == "protocols":
        return "protocol", "claim"
    if folder == "countries":
        return "country", "claim"
    if folder == "categories":
        return "category", "claim"
    if name in ("online", "online-week", "fastest-100"):
        return "core", "socket-observed"
    return "core", "accepted"


def collect_explorer_feeds(repo_root: Path) -> list[dict]:
    """All plain feeds with live counts, meanings and trust labels."""
    feeds = bps.collect_feeds(repo_root)
    for feed in feeds:
        kind, trust = feed_kind(feed["rel"], feed["name"])
        en, fa = slice_meaning(feed["rel"], feed["name"])
        feed["kind"] = kind
        feed["trust"] = trust
        feed["meaning_en"] = en
        feed["meaning_fa"] = fa
    return feeds


def load_workflows(repo_root: Path) -> list[dict]:
    """Scheduled automation, from the workflow files themselves.

    Each entry: (name, schedule, what it does). Only the workflows with
    a schedule or a dispatch that runs the pipeline are listed.
    """
    entries = [
        ("update-subs.yml", "0 */6 * * *",
         "Aggregation with socket checks; publishes the feeds.",
         "جمع‌آوری با تست سوکت؛ انتشار فیدها."),
        ("tier1-pattn-test.yml", "0 */2 * * *",
         "Tier-1 Realping testing on GitHub; publishes the tested feeds.",
         "تست Realping tier-1 در GitHub؛ انتشار فیدهای تست‌شده."),
        ("daily-deep-discovery.yml", "0 2 * * *",
         "Bounded discovery of new sources (500 API requests / 5400 s / 2000 channels).",
         "کشف محدود منابع جدید (۵۰۰ درخواست API / ۵۴۰۰ ثانیه / ۲۰۰۰ کانال)."),
        ("nightly-snapshot.yml", "0 3 * * *",
         "Nightly full-snapshot verification.",
         "اعتبارسنجی کامل شبانه اسنپ‌شات."),
        ("public-site.yml", "17 */6 * * * + on data pushes",
         "Rebuilds the public site and this explorer; deploys to GitHub Pages.",
         "بازسازی سایت عمومی و این کاوشگر؛ انتشار در GitHub Pages."),
        ("build-tier2.yml", "on demand",
         "Builds the tier-2 Windows tester (.exe) as a workflow artifact.",
         "ساخت تست‌کننده ویندوزی tier-2 (فایل .exe) به‌عنوان مصنوع workflow."),
        ("ci.yml", "on push / PR",
         "Matrix tests (3.12/3.14), privacy gate, byte-compile; contents: read.",
         "تست‌های ماتریسی (۳٫۱۲/۳٫۱۴)، دروازه حریم خصوصی، بایت‌کامپایل؛ دسترسی خواندنی."),
        ("provider-smoke-test.yml", "on demand",
         "Smoke-tests the model providers (OpenRouter + NVIDIA direct).",
         "تست دودی ارائه‌دهندگان مدل (OpenRouter و NVIDIA مستقیم)."),
    ]
    workflows_dir = repo_root / ".github" / "workflows"
    out = []
    for name, schedule, en, fa in entries:
        if (workflows_dir / name).exists():
            out.append({"name": name, "schedule": schedule,
                        "en": en, "fa": fa})
    return out

# The pipeline, stage by stage. Each stage: (title, subtitle, detail),
# each as (en, fa). The first seven are the aggregation pipeline (the
# original explorer's stages); tier-1, tier-2 and mirror/site extend it
# to the full flow the brief asks for.
PIPELINE_STAGES = [
    ("Sources registry",
     "منابع",
     "sources.json — the static registry",
     "Enabled, validated entries from the static registry. A failing or "
     "empty registry aborts the run and keeps the previous publication — "
     "zero enabled sources is a hard error, not an empty feed.",
     "ورودی‌های فعال و اعتبارسنجی‌شده از رجیستری ثابت. رجیستری خراب یا خالی "
     "اجرا را متوقف و انتشار قبلی را نگه می‌دارد — صفر منبع فعال یک خطای "
     "سخت است، نه یک فید خالی."),
    ("Concurrent fetch",
     "دریافت همزمان",
     "fetch_all_sources — bounded transport",
     "Public feeds are fetched concurrently with bounded workers, "
     "timeouts, and machine-only destinations (DNS / redirect / "
     "destination allow-lists). Source identities are claims, not "
     "endorsements.",
     "فیدهای عمومی به‌صورت همزمان با کارگران محدود، مهلت زمانی و مقصدهای "
     "فقط ماشینی دریافت می‌شوند (فهرست مجاز DNS / تغییرمسیر / مقصد). "
     "هویت منابع ادعاست، نه تأیید."),
    ("Parse & complete identity",
     "تجزیه و هویت کامل",
     "parsers.py + models.py — fingerprint v1",
     "Each supported node is parsed losslessly into a complete versioned "
     "identity (v1 fingerprint). Unsupported parameters are rejected — "
     "never stripped silently.",
     "هر گره پشتیبانی‌شده بدون افت به یک هویت کامل نسخه‌دار (اثر انگشت v1) "
     "تجزیه می‌شود. پارامترهای پشتیبانی‌نشده رد می‌شوند — هرگز بی‌صدا حذف "
     "نمی‌شوند."),
    ("Semantic deduplication",
     "حذف تکرار معنایی",
     "deduplicate_nodes",
     "Source-claim merging over complete fingerprints condenses the raw "
     "yield to unique connections.",
     "ادغام ادعاهای منابع بر اساس اثر انگشت کامل، خروجی خام را به اتصال‌های "
     "یکتا فشرده می‌کند."),
    ("Socket healthcheck",
     "تست سلامت سوکت",
     "check_nodes_health — socket only",
     "Bounded probes (concurrency 64, timeout 3.5s) on up to 1000 "
     "prioritized nodes; UDP-style schemes (hysteria2, tuic, wireguard) "
     "are excluded. A socket answer is a dated observation — not "
     "credential verification, not end-to-end proof.",
     "تست‌های محدود (همزمانی ۶۴، مهلت ۳٫۵ ثانیه) روی حداکثر ۱۰۰۰ گره "
     "اولویت‌دار؛ طرح‌های UDP (hysteria2 ،tuic ،wireguard) حذف می‌شوند. "
     "جواب سوکت یک مشاهده تاریخ‌دار است — نه اعتبارسنجی هویت و نه مدرک "
     "سرتاسری."),
    ("Categorize & render",
     "دسته‌بندی و رندر",
     "categorizer.py — outputs + counts",
     "balanced-500 samples protocol-balanced up to 500 rows; fastest-100 "
     "ranks the online pool by connection latency; country/category feeds "
     "are upstream claims. Round-trip render validation; one snapshot "
     "timestamp per publication.",
     "balanced-500 تا ۵۰۰ ردیف با توازن پروتکل نمونه می‌گیرد؛ fastest-100 "
     "استخر online را بر اساس تأخیر اتصال رتبه‌بندی می‌کند؛ فیدهای کشور/دسته "
     "ادعای منبع هستند. اعتبارسنجی رندر رفت‌وبرگشتی؛ یک زمان اسنپ‌شات برای "
     "هر انتشار."),
    ("Tier-1 Realping",
     "تست Realping tier-1",
     "tier1_pattn.py — every 2 hours on GitHub",
     "PattN WebAPI Realping-tests up to 8,000 due configs per run: an "
     "end-to-end HTTPS GET through the proxy, twice 100 ms apart, 5 s "
     "connect timeout. Passers become the tested feeds; the state is "
     "pruned to live feeds so it stays bounded.",
     "WebAPI برنامه PattN تا ۸٬۰۰۰ کانفیگ سررسید در هر اجرا را Realping "
     "تست می‌کند: یک GET سراسری HTTPS از طریق پروکسی، دو بار با فاصله ۱۰۰ "
     "میلی‌ثانیه، مهلت اتصال ۵ ثانیه. گذرنده‌ها فیدهای tested می‌شوند؛ "
     "وضعیت به فیدهای زنده هرس می‌شود تا محدود بماند."),
    ("Tier-2 Iran testing",
     "تست tier-2 از ایران",
     "tier2_pattn.py — owner PC, owner-triggered",
     "The same Realping test run from inside Iran on the owner's PC. "
     "Passers become the tested-iran feeds; configs that fail on GitHub "
     "but pass from Iran are tracked as GitHub-blocked and force-included "
     "in tier 1.",
     "همان تست Realping از داخل ایران روی رایانه مالک اجرا می‌شود. گذرنده‌ها "
     "فیدهای tested-iran می‌شوند؛ کانفیگ‌هایی که در GitHub رد می‌شوند ولی "
     "از ایران می‌گذرند به‌عنوان مسدودشده در GitHub ردیابی و در tier-1 "
     "اجباری گنجانده می‌شوند."),
    ("Journaled publish",
     "انتشار ژورنالی",
     "storage.py + public_git.py gate",
     "The validated feed set replaces the public feeds atomically with "
     "interrupted-write journaling; staged files pass a quiet role gate "
     "before commit; pushes are non-forced rebases that retain human "
     "commits.",
     "مجموعه فید اعتبارسنجی‌شده به‌صورت اتمی با ژورنال‌نویسی جایگزین "
     "فیدهای عمومی می‌شود؛ فایل‌های مرحله‌بندی‌شده پیش از کامیت از دروازه "
     "نقش عبور می‌کنند؛ پوش‌ها ری‌بیس غیراجباری‌اند که کامیت‌های انسانی را "
     "حفظ می‌کنند."),
    ("Mirror & site",
     "آینه و سایت",
     "ArvanCloud mirror + public site",
     "Base64 feeds sync to ArvanCloud Object Storage (accessible inside "
     "Iran) every 6 hours, and the public site and this explorer rebuild "
     "on every data push, so the live pages always match the repo.",
     "فیدهای Base64 هر ۶ ساعت به ArvanCloud Object Storage (قابل دسترس از "
     "داخل ایران) همگام می‌شوند و سایت عمومی و این کاوشگر با هر پوش داده "
     "بازسازی می‌شوند، پس صفحات زنده همیشه با مخزن هماهنگ‌اند."),
]

# Repo anatomy, grouped. Each item: (path, role, note en, note fa).
# Paths are checked at build time; a missing path is skipped (the test
# pins the group titles instead of every path).
ANATOMY = [
    ("Pipeline implementation", "پیاده‌سازی پایپ‌لاین", [
        ("src/main.py", "Orchestration",
         "CLI entry; load registry → concurrent fetch → identity/dedup → "
         "optional socket check → publish.",
         "ورودی CLI؛ بارگذاری رجیستری ← دریافت همزمان ← هویت/حذف تکرار ← "
         "تست سوکت اختیاری ← انتشار."),
        ("src/network.py", "Transport",
         "Bounded transport: public destinations, DNS & redirect "
         "allow-lists, request deadlines.",
         "حمل محدود: مقصدهای عمومی، فهرست مجاز DNS و تغییرمسیر، مهلت درخواست."),
        ("src/fetcher.py", "Ingress",
         "Concurrent feed fetch against the static registry; per-source "
         "success/failure accounting.",
         "دریافت همزمان فید از رجیستری ثابت؛ حساب موفقیت/شکست برای هر منبع."),
        ("src/parsers.py", "Parsing",
         "Lossless supported-node parsing; unsupported parameters rejected "
         "instead of stripped.",
         "تجزیه بدون افت گره‌های پشتیبانی‌شده؛ پارامترهای پشتیبانی‌نشده رد "
         "می‌شوند."),
        ("src/dedup.py", "Dedupe",
         "Source-claim merging: raw nodes → unique fingerprints. No "
         "manufactured variants.",
         "ادغام ادعاهای منبع: گره‌های خام ← اثر انگشت یکتا. بدون گونه ساخته‌شده."),
        ("src/healthcheck.py", "Boundary",
         "Standalone bounded socket probes (not credential verification).",
         "تست‌های سوکت محدود و مستقل (نه اعتبارسنجی هویت)."),
        ("src/categorizer.py", "Publish",
         "Evidence-bounded outputs; round-trip validation; one snapshot "
         "timestamp.",
         "خروجی‌های محدود به مدرک؛ اعتبارسنجی رفت‌وبرگشتی؛ یک زمان اسنپ‌شات."),
        ("src/storage.py", "Durability",
         "Atomic feed/checkpoint replacement; journaled recovery for "
         "interrupted publishers.",
         "جایگزینی اتمی فید/چک‌پوینت؛ بازیابی ژورنالی برای انتشار ناتمام."),
        ("src/tier1.py", "Tier 1",
         "Backoff, state, variants, results (pure, tested).",
         "تأخیر تصاعدی، وضعیت، گونه‌ها، نتایج (خالص، تست‌شده)."),
        ("src/pattn_client.py", "Tier client",
         "WebAPI client + launcher, shared by both tiers.",
         "کلاینت و راه‌انداز WebAPI، مشترک بین هر دو tier."),
        ("src/iran_results.py", "Owner-ops",
         "Owner-admin path: manifest validation and dated sanitized public "
         "evidence.",
         "مسیر مالک-مدیر: اعتبارسنجی مانیفست و مدرک عمومی تاریخ‌دار و پاک‌سازی‌شده."),
    ]),
    ("Operations & gates", "عملیات و دروازه‌ها", [
        ("scripts/tier1_pattn.py", "Tier 1",
         "Tier-1 driver on the GitHub runner; filling system over 4 WebAPI "
         "servers.",
         "راننده tier-1 روی GitHub؛ سیستم پرکننده روی ۴ سرور WebAPI."),
        ("scripts/public_git.py", "Gate",
         "Quiet tracked/index role gate; exact staged-file audit; "
         "non-forced rebased pushes.",
         "دروازه بی‌صدا نقش tracked/index؛ حسابرسی دقیق فایل‌های مرحله‌بندی‌شده؛ "
         "پوش ری‌بیس غیراجباری."),
        ("scripts/build_public_site.py", "Site",
         "Generates the public site (self-contained, bilingual).",
         "سازنده سایت عمومی (خودکفا، دوزبانه)."),
        ("scripts/build_explorer.py", "Explorer",
         "Generates this page (self-contained, bilingual).",
         "سازنده این صفحه (خودکفا، دوزبانه)."),
        ("scripts/build_mirror_page.py", "Mirror",
         "Builds the bare mirror page for ArvanCloud Object Storage.",
         "سازنده صفحه ساده آینه برای ArvanCloud Object Storage."),
        ("tools/tier2/tier2_pattn.py", "Tier 2",
         "Owner-PC app + browser UI: Realping/UDP tests, Stop, live ETA.",
         "برنامه رایانه مالک + رابط مرورگر: تست Realping/UDP، توقف، ETA زنده."),
        ("tools/telegram_bot/bot.py", "Bot",
         "Owner controls via Telegram: status, feeds, discovery trigger.",
         "کنترل مالک از طریق تلگرام: وضعیت، فیدها، راه‌اندازی کشف."),
    ]),
    ("Offline test suite", "مجموعه تست آفلاین", [
        ("tests/test_pipeline.py", "Integration",
         "Offline end-to-end pipeline integration.",
         "یکپارچگی سرتاسری آفلاین پایپ‌لاین."),
        ("tests/test_core.py", "Core",
         "Core identity/dedup behavior.",
         "رفتار هویت/حذف تکرار هسته."),
        ("tests/test_tier1.py", "Tier 1",
         "Backoff, state pruning, variant counts.",
         "تأخیر تصاعدی، هرس وضعیت، شمار گونه‌ها."),
        ("tests/test_tier2.py", "Tier 2",
         "Tier-2 upload and slice-variant logic.",
         "منطق بارگذاری و گونه برش tier-2."),
        ("tests/test_public_site.py", "Site",
         "Public-site generator: self-contained, secret-free.",
         "سازنده سایت عمومی: خودکفا، بدون رمز."),
        ("tests/test_rebrand.py", "Hygiene",
         "Public naming/branding consistency.",
         "سازگاری نام‌گذاری/برندینگ عمومی."),
        ("tests/test_workflow_security.py", "Security",
         "Workflow permissions/pins (contents read vs write).",
         "مجوزها/پین‌های workflow (خواندنی در برابر نوشتنی)."),
    ]),
    ("Specifications & audit records", "مشخصات و سوابق حسابرسی", [
        ("docs/ARCHITECTURE.md", "Docs",
         "Component map and trust-boundary contracts relied on by this "
         "explainer.",
         "نقشه اجزا و قراردادهای مرز اعتماد که این کاوشگر بر آن تکیه دارد."),
        ("docs/MIGRATION.md", "Docs",
         "Provenance: hashes, exclusions, no fabricated fresh probes.",
         "منشأ: هش‌ها، حذف‌ها، بدون تست تازه ساختگی."),
        ("docs/migration-manifest.json", "Docs",
         "Machine-readable migration record for the offline snapshot.",
         "رکورد ماشینی مهاجرت برای اسنپ‌شات آفلاین."),
        ("TIERED-TESTING.md", "Docs",
         "How tier-1 and tier-2 Realping testing work.",
         "تست Realping tier-1 و tier-2 چگونه کار می‌کند."),
        ("docs/PUBLIC-SITE.md", "Docs",
         "How the public site and mirrors are published.",
         "سایت عمومی و آینه‌ها چگونه منتشر می‌شوند."),
    ]),
    ("Published feeds", "فیدهای منتشرشده", [
        ("subs/all.txt (+ base64)", "Feed",
         "Every accepted node (count is live in the Overview section).",
         "همه گره‌های پذیرفته‌شده (شمارش در بخش نمای کلی زنده است)."),
        ("subs/protocols/", "Feed",
         "Scheme-sliced feeds: vless, vmess, trojan, ss, hysteria2, tuic.",
         "فیدهای برش پروتکل: vless ،vmess ،trojan ،ss ،hysteria2 ،tuic."),
        ("subs/countries/", "Feed",
         "Upstream-claimed country slices — labels are claims, not a "
         "confirmed exit location.",
         "برش‌های کشور ادعاشده — برچسب‌ها ادعایند، نه مکان خروج تأییدشده."),
        ("subs/categories/", "Feed",
         "Parameter-claim selections (special, reality, iran-optimized, "
         "and more).",
         "گزینش‌های ادعای پارامتر (ویژه، reality ،iran-optimized و بیشتر)."),
    ]),
    ("Root documents", "اسناد ریشه", [
        ("README.md", "Manifest",
         "Feed catalog, snapshot stats table, usage limits, local dev.",
         "کاتالوگ فید، جدول آمار اسنپ‌شات، محدودیت‌ها، توسعه محلی."),
        ("STATUS.md", "Status",
         "Offline migration snapshot; not a live healthcheck record.",
         "اسنپ‌شات مهاجرت آفلاین؛ رکورد زنده تست سلامت نیست."),
        ("sources.json", "Registry",
         "Curated upstream registries (static input).",
         "رجیستری‌های بالادستی گزینش‌شده (ورودی ثابت)."),
        ("LICENSE", "Legal",
         "MIT license for this project; upstream attribution preserved.",
         "مجوز MIT برای این پروژه؛ انتساب بالادستی حفظ شده است."),
    ]),
]

# Trust boundaries and evidence (title, body), each (en, fa).
TRUST_NOTES = [
    ("Public, claims-labeled input only",
     "ورودی فقط عمومی و برچسب‌ادعا",
     "Feeds are produced from public, curated registries. Source "
     "identities and country/category tags are upstream claims — validated "
     "for identity shape, not endorsed or geo-checked. No private data is "
     "crawled.",
     "فیدها از رجیستری‌های عمومی و گزینش‌شده تولید می‌شوند. هویت منابع و "
     "برچسب‌های کشور/دسته ادعای منبع‌اند — از نظر شکل هویت اعتبارسنجی "
     "شده‌اند، نه تأیید و نه راستی‌آزمایی جغرافیایی. هیچ داده خصوصی خزیده "
     "نمی‌شود."),
    ("Privacy gate before commit",
     "دروازه حریم خصوصی پیش از کامیت",
     "Two quiet checks bound every writer: a tracked/index role gate and, "
     "immediately before commit, an exact staged-file audit.",
     "دو بررسی بی‌صدا هر نویسنده را محدود می‌کند: دروازه نقش tracked/index "
     "و درست پیش از کامیت، حسابرسی دقیق فایل‌های مرحله‌بندی‌شده."),
    ("Non-forced publication",
     "انتشار غیراجباری",
     "Pushes are non-forced rebased updates that retain human commits. "
     "Five failed rebase/push attempts then the writer stops — the last "
     "green publication stays in place.",
     "پوش‌ها به‌روزرسانی‌های ری‌بیس غیراجباری‌اند که کامیت‌های انسانی را حفظ "
     "می‌کنند. پس از پنج تلاش ناموفق ری‌بیس/پوش، نویسنده متوقف می‌شود — "
     "آخرین انتشار سبز سر جایش می‌ماند."),
    ("Socket observation ≠ evidence",
     "مشاهده سوکت ≠ مدرک",
     "online.txt is dated socket observations only; fastest-100 ranks by "
     "measured connection latency. Nothing here proves credential validity "
     "or carrier interoperability.",
     "online.txt فقط مشاهده‌های سوکت تاریخ‌دار است؛ fastest-100 بر اساس "
     "تأخیر اتصال اندازه‌گیری‌شده رتبه‌بندی می‌کند. هیچ‌چیز اینجا اعتبار "
     "هویت یا سازگاری حامل را ثابت نمی‌کند."),
    ("Tested means end-to-end, not a guarantee",
     "تست‌شده یعنی سرتاسری، نه تضمین",
     "tested.txt passed a real connection test on GitHub runners; "
     "tested-iran.txt passed the same test from inside Iran. A passing "
     "test is a dated observation — servers go offline, get overloaded or "
     "get blocked.",
     "tested.txt یک تست اتصال واقعی در GitHub را گذرانده است؛ tested-iran.txt "
     "همان تست را از داخل ایران گذرانده است. تست گذرنده یک مشاهده تاریخ‌دار "
     "است — سرورها خاموش می‌شوند، شلوغ می‌شوند یا مسدود می‌شوند."),
    ("Protected Iran-results boundary",
     "مرز محافظت‌شده نتایج ایران",
     "Any end-to-end owner evidence lives on the protected iran-results "
     "branch: reviewed tester import, pinned core, explicit attestations, "
     "and an operator-only command. No scheduled job, bot, or dependency "
     "can publish there.",
     "هر مدرک سرتاسری مالک در شاخه محافظت‌شده iran-results زندگی می‌کند: "
     "واردات تست‌کننده بررسی‌شده، هسته پین‌شده، گواهی‌های صریح و دستور "
     "فقط اپراتور. هیچ کار زمان‌بندی‌شده، ربات یا وابستگی نمی‌تواند آنجا "
     "منتشر کند."),
    ("One writer per output directory",
     "یک نویسنده برای هر پوشه خروجی",
     "Actions concurrency serializes scheduled master writers, but local "
     "publishers are not covered. Concurrent writers are forbidden; "
     "journal recovery makes interrupted replacement recoverable, not "
     "parallel.",
     "همزمانی Actions نویسنده‌های زمان‌بندی‌شده master را سریالی می‌کند، ولی "
     "ناشران محلی را پوشش نمی‌دهد. نویسنده همزمان ممنوع است؛ بازیابی ژورنال "
     "جایگزینی ناتمام را قابل بازیابی می‌کند، نه موازی."),
    ("Minimal dependency surface",
     "حداقل سطح وابستگی",
     "Runtime: Python 3.12+ standard library only — no third-party "
     "dependencies to supply-chain-review. The project does not ship, "
     "install, or discover cores.",
     "اجرا: فقط کتابخانه استاندارد Python 3.12+ — بدون وابستگی ثالث برای "
     "بازبینی زنجیره تأمین. این پروژه هسته ارسال، نصب یا کشف نمی‌کند."),
]

# UI strings: English + Persian. Keys are shared; values are (en, fa).
STRINGS = {
    "tagline": (
        "Interactive explainer of the NavaRoute pipeline: data flow, "
        "automation, repo anatomy, published feeds, and trust boundaries.",
        "کاوشگر تعاملی پایپ‌لاین NavaRoute: جریان داده، خودکارسازی، آناتومی "
        "مخزن، فیدهای منتشرشده و مرزهای اعتماد.",
    ),
    "updated": ("Last updated", "آخرین به‌روز‌رسانی"),
    "sources": ("Sources", "منابع"),
    "configs": ("Configs", "کانفیگ‌ها"),
    "online": ("Online", "آنلاین"),
    "tested_passed": ("Tested", "تست‌شده"),
    "overview": ("What NavaRoute is — and is not", "NavaRoute چیست — و چه نیست"),
    "overview_is": (
        "NavaRoute publishes validated public URI subscriptions, not "
        "executable core configurations or client integration. Source "
        "claims, dated socket observations and dated owner end-to-end "
        "observations are separate contracts.",
        "NavaRoute اشتراک‌های URI عمومی اعتبارسنجی‌شده منتشر می‌کند، نه "
        "پیکربندی اجرایی هسته یا یکپارچگی کلاینت. ادعاهای منبع، مشاهده‌های "
        "سوکت تاریخ‌دار و مشاهده‌های سرتاسری تاریخ‌دار مالک قراردادهای "
        "جداگانه‌اند.",
    ),
    "overview_is_list": (
        "<li>A public aggregator: it collects public proxy configs from "
        "curated sources, deduplicates them, and publishes subscription "
        "feeds.</li>"
        "<li>Evidence-bounded: every label says what was actually observed "
        "(socket answer, Realping pass) — never more.</li>"
        "<li>Open and reproducible: Python 3.12+ standard library only, "
        "no third-party dependencies, everything in the repo.</li>",
        "<li>یک جمع‌آوری‌کننده عمومی: کانفیگ‌های عمومی پروکسی را از منابع "
        "گزینش‌شده جمع می‌کند، تکرارها را حذف می‌کند و فید اشتراک منتشر "
        "می‌کند.</li>"
        "<li>محدود به مدرک: هر برچسب می‌گوید چه چیزی واقعاً مشاهده شده است "
        "(جواب سوکت، گذر Realping) — هرگز بیشتر.</li>"
        "<li>باز و قابل بازتولید: فقط کتابخانه استاندارد Python 3.12+، بدون "
        "وابستگی ثالث، همه‌چیز در مخزن.</li>",
    ),
    "overview_is_not_list": (
        "<li>Not a VPN or a server: NavaRoute runs no servers and sells "
        "nothing.</li>"
        "<li>Not a guarantee: a listed config may be slow, dead, or "
        "blocked. Try the tested feed first.</li>"
        "<li>Not a client: use v2rayNG, v2rayN, Hiddify or a compatible "
        "app to connect.</li>",
        "<li>VPN یا سرور نیست: NavaRoute سروری اجرا نمی‌کند و چیزی "
        "نمی‌فروشد.</li>"
        "<li>تضمین نیست: یک کانفیگ فهرست‌شده ممکن است کند، مرده یا مسدود "
        "باشد. اول فید tested را امتحان کنید.</li>"
        "<li>کلاینت نیست: برای اتصال از v2rayNG ،v2rayN ،Hiddify یا برنامه "
        "سازگار استفاده کنید.</li>",
    ),
    "pipeline": ("The data pipeline, stage by stage", "پایپ‌لاین داده، مرحله به مرحله"),
    "pipeline_sub": (
        "From a curated source list to published feeds — every stage is "
        "bounded, and every label says what was actually observed.",
        "از فهرست منبع گزینش‌شده تا فیدهای منتشرشده — هر مرحله محدود است و "
        "هر برچسب می‌گوید چه چیزی واقعاً مشاهده شده است.",
    ),
    "automation": ("Scheduled GitHub Actions", "اکشن‌های زمان‌بندی‌شده GitHub"),
    "automation_sub": (
        "The pipeline runs on GitHub Actions. Writers share one "
        "concurrency group so only one publishes at a time.",
        "پایپ‌لاین روی GitHub Actions اجرا می‌شود. نویسنده‌ها یک گروه همزمانی "
        "مشترک دارند تا هر بار فقط یکی منتشر کند.",
    ),
    "anatomy": ("Every part of the repo", "هر بخش مخزن"),
    "anatomy_sub": (
        "What each file does. Paths are checked at build time — a path "
        "that no longer exists is left out.",
        "هر فایل چه می‌کند. مسیرها هنگام ساخت بررسی می‌شوند — مسیری که دیگر "
        "وجود ندارد حذف می‌شود.",
    ),
    "feeds": ("The published feeds", "فیدهای منتشرشده"),
    "feeds_sub": (
        "Every plain feed in the repo, with live row counts. Use the "
        "search box or the group buttons to narrow it down. Each feed has "
        "a Base64 twin with the same rows.",
        "همه فیدهای ساده مخزن با شمار زنده ردیف‌ها. با جستجو یا دکمه‌های گروه "
        "محدودش کنید. هر فید یک همتای Base64 با همان ردیف‌ها دارد.",
    ),
    "search_feeds": ("Search feeds…", "جستجوی فیدها…"),
    "feed": ("Feed", "فید"),
    "count": ("Rows", "ردیف‌ها"),
    "kind": ("Kind", "نوع"),
    "trust": ("Trust", "اعتماد"),
    "meaning": ("What it means", "معنی آن چیست"),
    "tested_badge": ("Tested", "تست‌شده"),
    "trust_title": ("Trust boundaries and evidence", "مرزهای اعتماد و مدرک"),
    "dev": ("Running it yourself", "اجرای آن توسط خودتان"),
    "dev_body": (
        "Clone the repo (Python 3.12+, standard library only — nothing to "
        "install). Run the tests, then build the site and this explorer:",
        "مخزن را کلون کنید (Python 3.12+، فقط کتابخانه استاندارد — چیزی برای "
        "نصب نیست). تست‌ها را اجرا کنید، سپس سایت و این کاوشگر را بسازید:",
    ),
    "footer": (
        "NavaRoute is an open-source aggregator of public proxy configs. "
        "It does not run servers, sell access, or promise that any config works.",
        "NavaRoute یک جمع‌آوری‌کننده متن‌باز کانفیگ‌های عمومی پروکسی است. "
        "سرور اجرا نمی‌کند، دسترسی نمی‌فروشد و کار کردن هیچ کانفیگی را تضمین نمی‌کند.",
    ),
    "repo_link": ("GitHub repository", "مخزن GitHub"),
    "site_link": ("Public site", "سایت عمومی"),
    "issues_link": ("Report a problem", "گزارش مشکل"),
}

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
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.box{background:#111c33;border:1px solid #1e3a5f;border-radius:12px;padding:16px}
.box h3{margin:0 0 8px}.box ul{margin:0;padding-inline-start:20px;color:#cbd5e1}
.stages{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.stage{background:#111c33;border:1px solid #1e3a5f;border-radius:12px;padding:16px}
.stage .num{display:inline-block;background:#0369a1;color:#fff;border-radius:999px;
width:26px;height:26px;line-height:26px;text-align:center;font-size:.85em;margin-inline-end:8px}
.stage h3{margin:0 0 2px;display:inline;font-size:1.05em}
.stage .sub2{color:#7dd3fc;font-size:.85em;margin:2px 0 8px}
.stage p{margin:0;color:#cbd5e1;font-size:.95em}
.search{width:100%;padding:10px 12px;border-radius:10px;border:1px solid #475569;background:#0f172a;
color:#e5e7eb;font-size:15px;margin-bottom:12px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}
.chip{background:#1e293b;border:1px solid #475569;color:#e5e7eb;border-radius:999px;padding:5px 13px;
cursor:pointer;font-size:14px}.chip.on{background:#0369a1;border-color:#0369a1}
.tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:.92em}
@media(max-width:600px){table{font-size:.85em}th,td{padding:6px 4px}}
th,td{padding:8px 6px;border-bottom:1px solid #1e293b;text-align:start;vertical-align:top}
th{color:#94a3b8}td code{font-size:.9em;color:#bae6fd;word-break:break-all}
tr.hidden{display:none}
.badge{display:inline-block;background:#14532d;color:#bbf7d0;border-radius:999px;padding:1px 9px;font-size:.8em;margin-inline-start:6px}
.badge.dim{background:#334155;color:#cbd5e1}
details{background:#111c33;border:1px solid #1e3a5f;border-radius:12px;padding:12px 16px;margin-bottom:10px}
details summary{cursor:pointer;font-weight:600}
details p{margin:8px 0 0;color:#cbd5e1}
pre{background:#0f172a;border:1px solid #1e3a5f;border-radius:12px;padding:14px;overflow-x:auto;
color:#bae6fd;font-size:.9em}
footer{margin-top:48px;border-top:1px solid #1e3a5f;padding-top:16px;color:#94a3b8;font-size:.9em;text-align:center}
.i18n-fa{display:none}body[dir=rtl] .i18n-en{display:none}body[dir=rtl] .i18n-fa{display:inline}
body[dir=rtl] h1,body[dir=rtl] h2,body[dir=rtl] h3,body[dir=rtl] p,body[dir=rtl] summary,body[dir=rtl] td,body[dir=rtl] th{display:block}
body[dir=rtl] td,body[dir=rtl] th{display:table-cell}
"""

JS = """
function setLang(l){document.body.dir=(l==='fa')?'rtl':'ltr';document.documentElement.lang=l;
document.getElementById('langBtn').textContent=(l==='fa')?'English | فارسی':'فارسی | English';
try{localStorage.setItem('navaroute-lang',l)}catch(e){}}
var group='all';
function setGroup(g,el){group=g;document.querySelectorAll('.chip').forEach(function(c){c.classList.remove('on')});
el.classList.add('on');applyFilter()}
function applyFilter(){var q=document.getElementById('q').value.toLowerCase();
document.querySelectorAll('#feedTable tbody tr').forEach(function(tr){
var okG=(group==='all'||tr.dataset.group===group);
var hay=(tr.dataset.name+' '+tr.dataset.meaning+' '+tr.dataset.kind).toLowerCase();
var okQ=!q||hay.indexOf(q)>=0;
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


def _pair(en: str, fa: str) -> str:
    return (
        f'<span class="i18n-en">{html.escape(en)}</span>'
        f'<span class="i18n-fa">{html.escape(fa)}</span>'
    )


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _anatomy_path_exists(repo_root: Path, path: str) -> bool:
    """Anatomy entries name files or directories; strip the base64 note."""
    clean = path.replace(" (+ base64)", "")
    target = repo_root / clean
    return target.exists()


def render_html(repo_root: Path, feeds: list[dict], snapshot: dict,
                sources: int, tier1: dict, iran: dict,
                workflows: list[dict]) -> str:
    iran = iran or {}
    tested_total = tier1.get("all", {}).get("passed", 0)
    snapshot_line = snapshot["snapshot_time"] or "—"

    def tested_badge(feed: dict) -> str:
        group = variant_group(feed["name"])
        if group is None:
            return ""
        if feed["name"] == "tested-iran" or feed["name"].endswith("-tested-iran"):
            info = iran.get(group)
            label = "Iran"
        else:
            info = tier1.get(group)
            label = "T1"
        if not info or not info["passed"]:
            return ""
        return (f'<span class="badge">{label} ✓ '
                f'{info["passed"]}/{info["tested"]}</span>')

    stages = "".join(
        f"""<div class="stage"><span class="num">{i}</span><h3>{_pair(t, tf)}</h3>
<div class="sub2">{_esc(s)}</div><p>{_pair(d, df)}</p></div>"""
        for i, (t, tf, s, d, df) in enumerate(PIPELINE_STAGES, 1)
    )
    workflow_rows = "".join(
        f"""<tr><td><code>{_esc(w["name"])}</code></td><td>{_esc(w["schedule"])}</td>"""
        f"""<td>{_pair(w["en"], w["fa"])}</td></tr>"""
        for w in workflows
    )
    anatomy_sections = ""
    for group_title, group_title_fa, items in ANATOMY:
        rows = ""
        for path, role, note_en, note_fa in items:
            if not _anatomy_path_exists(repo_root, path):
                continue
            rows += (f"""<tr><td><code>{_esc(path)}</code></td><td>{_esc(role)}</td>"""
                     f"""<td>{_pair(note_en, note_fa)}</td></tr>""")
        if not rows:
            continue
        anatomy_sections += (
            f"""<h3>{_pair(group_title, group_title_fa)}</h3>"""
            f"""<div class="tablewrap"><table><thead><tr><th>Path</th><th>Role</th>"""
            f"""<th>What it does</th></tr></thead><tbody>{rows}</tbody></table></div>"""
        )
    feed_rows = "".join(
        f"""<tr data-group="{feed["kind"]}" data-name="{_esc(feed["name"])}" """
        f"""data-meaning="{_esc(feed["meaning_en"])}" data-kind="{feed["kind"]}">"""
        f"""<td><code>{_esc(feed["rel"])}</code>{tested_badge(feed)}</td>"""
        f"""<td>{feed["count"]:,}</td><td>{feed["kind"]}</td>"""
        f"""<td>{feed["trust"]}</td>"""
        f"""<td>{_pair(feed["meaning_en"], feed["meaning_fa"])}</td>"""
        f"""<td><a href="{feed["url"]}">raw</a>"""
        + (f""" · <a href="{feed["b64_url"]}">b64</a>""" if feed["b64_url"] else "")
        + "</td></tr>"
        for feed in feeds
    )
    kinds = ["core", "tested", "tested-iran", "protocol", "country", "category"]
    chips = "".join(
        f"""<button class="chip" onclick="setGroup('{k}',this)">{k}</button>"""
        for k in kinds
    )
    trust_notes = "".join(
        f"""<details><summary>{_pair(t, tf)}</summary><p>{_pair(b, bf)}</p></details>"""
        for t, tf, b, bf in TRUST_NOTES
    )
    return f"""<!doctype html>
<html lang="fa" dir="rtl">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/svg+xml" href="./favicon.svg">
<title>NavaRoute Pipeline Explorer</title>
<meta name="description" content="Interactive explainer of the NavaRoute pipeline: data flow, automation, repo anatomy, published feeds, and trust boundaries.">
<style>{CSS}</style></head>
<body dir="rtl">
<div class="lang"><button id="langBtn" onclick="setLang(document.body.dir==='rtl'?'en':'fa')">English | فارسی</button></div>
<div class="wrap">
<header><h1>NavaRoute Pipeline Explorer</h1><p class="tag">{_t("tagline")}</p></header>
<div class="stats">
<div class="stat"><b>{sources:,}</b><span>{_t("sources")}</span></div>
<div class="stat"><b>{snapshot["accepted"]:,}</b><span>{_t("configs")}</span></div>
<div class="stat"><b>{snapshot["reachable"]:,}</b><span>{_t("online")}</span></div>
<div class="stat"><b>{tested_total:,}</b><span>{_t("tested_passed")}</span></div>
</div>
<p class="updated">{_t("updated")}: {snapshot_line}</p>

<h2>{_t("overview")}</h2>
<p>{_t("overview_is")}</p>
<div class="two">
<div class="box"><h3>✓</h3><ul>{_t("overview_is_list")}</ul></div>
<div class="box"><h3>✗</h3><ul>{_t("overview_is_not_list")}</ul></div>
</div>

<h2>{_t("pipeline")}</h2>
<p class="sub">{_t("pipeline_sub")}</p>
<div class="stages">{stages}</div>

<h2>{_t("automation")}</h2>
<p class="sub">{_t("automation_sub")}</p>
<div class="tablewrap"><table><thead><tr><th>Workflow</th><th>Schedule</th><th>What it does</th></tr></thead>
<tbody>{workflow_rows}</tbody></table></div>

<h2>{_t("anatomy")}</h2>
<p class="sub">{_t("anatomy_sub")}</p>
{anatomy_sections}

<h2>{_t("feeds")}</h2>
<p class="sub">{_t("feeds_sub")}</p>
<input id="q" class="search" placeholder="{_esc("Search feeds… / جستجوی فیدها…")}" oninput="applyFilter()">
<div class="chips"><button class="chip on" onclick="setGroup('all',this)">all</button>{chips}</div>
<div class="tablewrap"><table id="feedTable"><thead><tr><th>{_t("feed")}</th><th>{_t("count")}</th><th>{_t("kind")}</th><th>{_t("trust")}</th><th>{_t("meaning")}</th><th>Links</th></tr></thead>
<tbody>{feed_rows}</tbody></table></div>

<h2>{_t("trust_title")}</h2>
{trust_notes}

<h2>{_t("dev")}</h2>
<p>{_t("dev_body")}</p>
<pre>git clone https://github.com/mani-moh/navaroute.git
cd navaroute
python3 -m unittest discover -s tests
python3 -m ruff check src scripts tests tools
python3 scripts/build_public_site.py --repo-root . --out site
python3 scripts/build_explorer.py --repo-root . --out docs/explorer</pre>

<footer><p>{_t("footer")}</p>
<p><a href="{REPO_URL}">{_t("repo_link")}</a> · <a href="{SITE_URL}">{_t("site_link")}</a> ·
<a href="{REPO_URL}/issues">{_t("issues_link")}</a></p></footer>
</div>
<script>{JS}</script>
</body></html>
"""


def build(repo_root: Path, out_dir: Path) -> Path:
    feeds = collect_explorer_feeds(repo_root)
    snapshot = bps.parse_snapshot(repo_root)
    sources = bps.load_sources_count(repo_root)
    tier1 = bps.load_tier1(repo_root)
    iran = bps.load_iran(repo_root)
    workflows = load_workflows(repo_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(
        render_html(repo_root, feeds, snapshot, sources, tier1, iran,
                    workflows),
        encoding="utf-8",
    )
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the NavaRoute Pipeline Explorer.")
    parser.add_argument("--repo-root", default=".", type=Path)
    parser.add_argument("--out", default="docs/explorer", type=Path)
    args = parser.parse_args(argv)
    out_path = build(args.repo_root.resolve(), args.out)
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
