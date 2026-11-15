"""Tests for the Pipeline Explorer generator (scripts/build_explorer.py)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_explorer as be  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# Old feed names (PR #45 clean break) that must never appear in the new
# explorer page: the old feed filenames and the old feed-path prefixes.
# Bare "active" is NOT in this list: it legitimately appears inside the
# word "Interactive" (the page title/tagline).
OLD_FEED_NAMES = (
    "reachable.txt", "active.txt", "top100.txt", "lite500.txt",
    "verified.txt",
    "tier1_all", "tier1_reachable", "tier1_active", "tier1_top100",
    "tier1_lite500",
    "iran_all", "iran_reachable", "iran_active", "iran_top100",
    "iran_lite500",
    "subs/tier1_", "subs/iran_",
)
# Bare old words the page also avoids (it uses "online", "tested" and
# "socket check" instead).
OLD_WORDS = ("reachable", "verified", "top100", "lite500")
NEW_FEED_NAMES = (
    "online.txt", "online-week.txt", "fastest-100.txt",
    "balanced-500.txt", "tested.txt",
)


def _make_repo(tmp: Path) -> Path:
    """A minimal fake repo root with core + slice feeds and tier-1 results."""
    subs = tmp / "subs"
    (subs / "protocols").mkdir(parents=True)
    (subs / "countries").mkdir(parents=True)
    (subs / "all.txt").write_text(
        "# comment\nvless://abc\n\nvmess://def\n", encoding="utf-8"
    )
    (subs / "all_base64.txt").write_text("dmxlc3M6Ly9hYmMK", encoding="utf-8")
    (subs / "online.txt").write_text("vless://abc\n", encoding="utf-8")
    (subs / "online-week.txt").write_text("vless://abc\n", encoding="utf-8")
    (subs / "fastest-100.txt").write_text("vless://abc\n", encoding="utf-8")
    (subs / "balanced-500.txt").write_text("vless://abc\n", encoding="utf-8")
    (subs / "tested.txt").write_text("vless://abc\n", encoding="utf-8")
    (subs / "protocols" / "vless.txt").write_text("vless://abc\n", encoding="utf-8")
    (subs / "countries" / "us.txt").write_text("vless://abc\n", encoding="utf-8")
    (tmp / "sources.json").write_text(
        json.dumps({"sources": [{"id": "a"}, {"id": "b"}]}), encoding="utf-8"
    )
    (tmp / "STATUS.md").write_text(
        "Snapshot time: 2026-11-14T00:00:00Z\n\n"
        "| Accepted Public Nodes | 2 |\n| Socket Reachable Nodes | 1 |\n",
        encoding="utf-8",
    )
    data = tmp / "data"
    data.mkdir()
    (data / "tier1_results.json").write_text(
        json.dumps(
            {
                "totals": {"tested": 1, "passed": 1},
                "feeds": {"all": {"tested": 1, "passed": 1}},
            }
        ),
        encoding="utf-8",
    )
    return tmp


class TestExplorer(unittest.TestCase):
    def test_collect_feeds_kinds_and_meanings(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d))
            feeds = be.collect_explorer_feeds(repo)
            by_name = {f["name"]: f for f in feeds}
            self.assertEqual(by_name["all"]["count"], 2)
            self.assertEqual(by_name["all"]["kind"], "core")
            self.assertEqual(by_name["all"]["trust"], "accepted")
            self.assertEqual(by_name["online"]["kind"], "core")
            self.assertEqual(by_name["online"]["trust"], "socket-observed")
            self.assertEqual(by_name["tested"]["kind"], "tested")
            self.assertEqual(by_name["tested"]["trust"], "end-to-end (GitHub)")
            self.assertEqual(by_name["vless"]["kind"], "protocol")
            self.assertEqual(by_name["us"]["kind"], "country")
            self.assertIn("VLESS", by_name["vless"]["meaning_en"])
            self.assertIn("US", by_name["us"]["meaning_en"])

    def test_slice_meaning_tested_variants(self) -> None:
        en, _fa = be.slice_meaning("protocols/vless-tested.txt", "vless-tested")
        self.assertIn("tier-1 Realping", en)
        en, _fa = be.slice_meaning("countries/us-tested-iran.txt", "us-tested-iran")
        self.assertIn("from inside Iran", en)
        en, _fa = be.slice_meaning("protocols/hysteria2.txt", "hysteria2")
        self.assertIn("Excluded from socket probing", en)

    def test_load_workflows_only_existing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d))
            # No .github/workflows in the fake repo: nothing listed.
            self.assertEqual(be.load_workflows(repo), [])
        real = be.load_workflows(REPO_ROOT)
        names = {w["name"] for w in real}
        self.assertIn("update-subs.yml", names)
        self.assertIn("tier1-pattn-test.yml", names)
        self.assertIn("public-site.yml", names)

    def test_render_has_no_old_feed_names(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d))
            page = be.build(repo, Path(d) / "explorer").read_text(encoding="utf-8")
            lowered = page.lower()
            for old in OLD_FEED_NAMES:
                self.assertNotIn(old, page, f"old feed name {old!r} in explorer page")
            for word in OLD_WORDS:
                self.assertNotIn(word, lowered, f"old word {word!r} in explorer page")
            for new in NEW_FEED_NAMES:
                self.assertIn(new, page, f"new feed name {new!r} missing")
            self.assertIn("NavaRoute Pipeline Explorer", page)
            self.assertIn("The data pipeline, stage by stage", page)
            self.assertIn("Tier-1 Realping", page)
            self.assertIn("Tier-2 Iran testing", page)
            self.assertIn("کاوشگر", page)

    def test_render_is_self_contained_and_secret_free(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d))
            page = be.build(repo, Path(d) / "explorer").read_text(encoding="utf-8")
            # No external resources: the page must load from any mirror.
            self.assertNotIn("<script src=", page)
            self.assertNotIn('<link rel="stylesheet"', page)
            self.assertNotIn("http://", page)
            for secret_word in ("GITHUB_TOKEN", "api_key", "password", "CLOUDFLARE"):
                self.assertNotIn(secret_word, page)

    def test_real_repo_smoke(self) -> None:
        """End-to-end smoke test against the real checkout."""
        with tempfile.TemporaryDirectory() as d:
            page = be.build(REPO_ROOT, Path(d) / "explorer").read_text(encoding="utf-8")
            lowered = page.lower()
            for old in OLD_FEED_NAMES:
                self.assertNotIn(old, page, f"old feed name {old!r} in explorer page")
            for word in OLD_WORDS:
                self.assertNotIn(word, lowered, f"old word {word!r} in explorer page")
            for new in NEW_FEED_NAMES:
                self.assertIn(new, page, f"new feed name {new!r} missing")
            self.assertIn("tested-iran", page)
            self.assertIn("138,970", page)
            self.assertIn("T1 ✓", page)
            self.assertIn("کاوشگر", page)


if __name__ == "__main__":
    unittest.main()
