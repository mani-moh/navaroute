"""Tests for the minimal mirror page generator (scripts/build_mirror_page.py)."""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_mirror_page as bmp  # noqa: E402

# Words the mirror page must never show. The page is deliberately bare:
# labels and a few-word descriptions only, nothing about the purpose.
FORBIDDEN = (
    "vpn", "proxy", "bypass", "censor", "filter", "config", "subscription",
    "navaroute", "xray", "vless", "vmess", "trojan", "shadowsocks",
    "hysteria", "tuic", "sing-box", "clash", "iran", "telegram",
)


def _visible_text(page: str) -> str:
    """Page text without tags, scripts, styles and onclick URLs."""
    page = re.sub(r"onclick=\"copyText\(this,'[^']*'\)\"", "", page)
    page = re.sub(r"<script>.*?</script>", "", page, flags=re.S)
    page = re.sub(r"<style>.*?</style>", "", page, flags=re.S)
    page = re.sub(r"<[^>]+>", " ", page)
    return page.lower()


class TestMirrorPage(unittest.TestCase):
    def test_builds_copy_links_and_skips_missing_feeds(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            feeds_dir = Path(d) / "feeds"
            feeds_dir.mkdir()
            (feeds_dir / "tier1_all_base64.txt").write_text("dmxlc3M6Ly8=\n")
            (feeds_dir / "top100_base64.txt").write_text("dmxlc3M6Ly8=\n")
            out = Path(d) / "site"
            out_path = bmp.build(feeds_dir, "https://example.invalid/files", out)
            page = out_path.read_text(encoding="utf-8")
            self.assertIn("https://example.invalid/files/tier1_all_base64.txt", page)
            self.assertIn("https://example.invalid/files/top100_base64.txt", page)
            self.assertIn("working", page)
            self.assertIn("top 100", page)
            # active_base64.txt is absent from the fixture: no "recent" row.
            self.assertNotIn("recent", _visible_text(page))

    def test_page_is_bare_and_self_contained(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            feeds_dir = Path(d) / "feeds"
            feeds_dir.mkdir()
            (feeds_dir / "tier1_all_base64.txt").write_text("dmxlc3M6Ly8=\n")
            out_path = bmp.build(feeds_dir, "https://example.invalid/files", Path(d) / "site")
            page = out_path.read_text(encoding="utf-8")
            self.assertNotIn("<script src=", page)
            self.assertNotIn('<link rel="stylesheet"', page)
            self.assertNotIn("http://", page)
            visible = _visible_text(page)
            for word in FORBIDDEN:
                self.assertNotIn(word, visible, f"forbidden word {word!r} on mirror page")

    def test_real_repo_feeds_build(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as d:
            out_path = bmp.build(
                repo_root / "subs", "https://example.invalid/files", Path(d) / "site"
            )
            page = out_path.read_text(encoding="utf-8")
            self.assertIn("tier1_all_base64.txt", page)
            self.assertIn("working", page)
            visible = _visible_text(page)
            for word in FORBIDDEN:
                self.assertNotIn(word, visible, f"forbidden word {word!r} on mirror page")


if __name__ == "__main__":
    unittest.main()
