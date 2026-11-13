"""Tests for the public-site generator (scripts/build_public_site.py)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_public_site as bps  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_repo(tmp: Path) -> Path:
    """A minimal fake repo root with one main feed + one tier-1 result."""
    subs = tmp / "subs"
    subs.mkdir(parents=True)
    (subs / "all.txt").write_text(
        "# comment\nvless://abc\n\nvmess://def\n", encoding="utf-8"
    )
    (subs / "all_base64.txt").write_text("dmxlc3M6Ly9hYmMK", encoding="utf-8")
    (subs / "tier1_all.txt").write_text("vless://abc\n", encoding="utf-8")
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


class TestPublicSite(unittest.TestCase):
    def test_count_uris_skips_comments_and_blanks(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "feed.txt"
            path.write_text("# head\nvless://x\n\nss://y\n# tail\n", encoding="utf-8")
            self.assertEqual(bps.count_uris(path), 2)

    def test_collect_feeds_groups_and_twins(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d))
            feeds = bps.collect_feeds(repo)
            by_name = {f["name"]: f for f in feeds}
            self.assertEqual(by_name["all"]["count"], 2)
            self.assertEqual(by_name["all"]["group"], "main")
            self.assertIsNotNone(by_name["all"]["b64_url"])
            self.assertEqual(by_name["tier1_all"]["group"], "tier")
            self.assertIsNone(by_name["tier1_all"]["b64_url"])
            self.assertTrue(by_name["all"]["url"].startswith(bps.RAW_BASE))
            self.assertTrue(by_name["all"]["mirror_url"].startswith(bps.MIRROR_BASE))

    def test_parse_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d))
            snap = bps.parse_snapshot(repo)
            self.assertEqual(snap["accepted"], 2)
            self.assertEqual(snap["reachable"], 1)
            self.assertEqual(snap["snapshot_time"], "2026-11-14T00:00:00Z")

    def test_load_tier1(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d))
            self.assertEqual(bps.load_tier1(repo), {"all": {"tested": 1, "passed": 1}})

    def test_render_is_self_contained_and_secret_free(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d))
            out = Path(d) / "site"
            out_path = bps.build(repo, out)
            page = out_path.read_text(encoding="utf-8")
            self.assertIn("tier1_all.txt", page)
            self.assertIn("v2rayNG", page)
            self.assertIn("Hiddify", page)
            self.assertIn("کپی لینک", page)
            self.assertIn("T1 ✓ 1/1", page)
            # No external resources: the page must load from any mirror.
            self.assertNotIn("<script src=", page)
            self.assertNotIn('<link rel="stylesheet"', page)
            self.assertNotIn("http://", page)
            for secret_word in ("GITHUB_TOKEN", "api_key", "password", "CLOUDFLARE"):
                self.assertNotIn(secret_word, page)

    def test_real_repo_smoke(self) -> None:
        """End-to-end smoke test against the real checkout."""
        with tempfile.TemporaryDirectory() as d:
            out_path = bps.build(REPO_ROOT, Path(d) / "site")
            page = out_path.read_text(encoding="utf-8")
            self.assertIn("tier1_all.txt", page)
            self.assertIn("v2rayNG", page)
            self.assertIn("Hiddify", page)
            self.assertIn("Streisand", page)
            self.assertIn("NekoBox", page)
            self.assertIn("cdn.jsdelivr.net", page)
            self.assertIn("کپی لینک", page)
            # Real tier-1 numbers are baked in.
            self.assertRegex(page, r"T1 ✓ \d+/\d+")


if __name__ == "__main__":
    unittest.main()
