"""Offline tests for parallel discovery prefetch, cooldowns, and GitHub tree search."""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.discovery.agent import DiscoveryAgent
from src.discovery.search_engine import WebSearchScraper
from src.discovery.state_manager import DiscoveryStateManager


class TestDiscoveryParallelAndYield(unittest.TestCase):
    def test_search_github_discovers_tree_paths_and_falls_back(self) -> None:
        scraper = WebSearchScraper()
        responses = {
            "search/repositories": json.dumps({"items": [{"full_name": "owner/repo", "default_branch": "main"}]}),
            "git/trees/main": json.dumps({
                "tree": [
                    {"type": "blob", "path": "README.md"},
                    {"type": "blob", "path": "hub/merged.txt"},
                    {"type": "blob", "path": "lists/vless.txt"},
                ]
            }),
        }

        def fake_read(url: str, max_bytes: int = 0) -> str:
            for key, body in responses.items():
                if key in url:
                    return body
            return ""

        with patch.object(scraper, "_read", side_effect=fake_read):
            urls = scraper.search_github("vless")
        self.assertEqual(urls, [
            "https://raw.githubusercontent.com/owner/repo/main/hub/merged.txt",
            "https://raw.githubusercontent.com/owner/repo/main/lists/vless.txt",
        ])

    def test_get_oldest_active_channels_respects_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = DiscoveryStateManager(state_file=str(Path(tmp) / "state.json"))
            now = time.time()
            mgr.state["sources_metadata"] = {
                "freshchan": {"handle": "freshchan", "is_active": True, "is_telegram": True,
                              "sub_url": "https://t.me/s/freshchan", "last_checked": now - 60},
                "stalechan": {"handle": "stalechan", "is_active": True, "is_telegram": True,
                              "sub_url": "https://t.me/s/stalechan", "last_checked": now - 50000},
            }
            self.assertEqual(mgr.get_oldest_active_channels(limit=5, min_age_seconds=3600), ["stalechan"])
            self.assertEqual(mgr.get_oldest_active_channels(limit=5, min_age_seconds=0), ["stalechan", "freshchan"])

    def test_parallel_prefetch_channels_populates_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = DiscoveryAgent(
                state_file=str(root / "state.json"),
                discovered_sources_file=str(root / "sources.json"),
                leaderboard_file=str(root / "DISCOVERY.md"),
                workers=4,
            )
            with patch("src.discovery.agent.TelegramChannelScraper.fetch_channel_page", return_value="<html>ok</html>") as mock_fetch:
                agent._prefetch_channels(["chan1", "chan2", "chan3"])
            self.assertEqual(mock_fetch.call_count, 3)
            self.assertEqual(len(agent._prefetched_pages), 3)


if __name__ == "__main__":
    unittest.main()
