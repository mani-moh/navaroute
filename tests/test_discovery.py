import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.discovery.agent import DiscoveryAgent
from src.discovery.nemotron_client import NemotronClient
from src.discovery.search_engine import TelegramChannelScraper, WebSearchScraper
from src.discovery.state_manager import DiscoveryStateManager
from src.fetcher import load_sources


class OfflineDiscoveryTestCase(unittest.TestCase):
    def setUp(self):
        temp_directory = tempfile.gettempdir()
        environment = patch.dict(os.environ, {"TEMP": temp_directory, "TMP": temp_directory}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        for target in ("urllib.request.urlopen", "urllib.request.OpenerDirector.open"):
            guard = patch(target, side_effect=AssertionError("unexpected external request"))
            transport = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(transport.assert_not_called)


class TestNemotronClient(OfflineDiscoveryTestCase):
    def test_client_init_defaults(self):
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "test-nvapi-key"}, clear=True):
            client = NemotronClient()
            self.assertEqual(client.api_key, "test-nvapi-key")
            self.assertEqual(client.model, "moonshotai/kimi-k2.6")
            self.assertEqual(client.base_url, "https://integrate.api.nvidia.com/v1")

    @patch("urllib.request.urlopen")
    def test_fallback_model_on_http_error(self, mock_urlopen):
        import urllib.error
        err = urllib.error.HTTPError("https://integrate.api.nvidia.com/v1/chat/completions", 404, "Not Found", {}, None)
        ok_resp = MagicMock()
        ok_resp.read.return_value = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "Fallback model succeeded"}}]
        }).encode("utf-8")
        mock_urlopen.side_effect = [err, MagicMock(__enter__=lambda s: ok_resp, __exit__=lambda s, *a: None)]

        client = NemotronClient(api_key="fake-key")
        out = client.complete("hello")
        self.assertEqual(out, "Fallback model succeeded")
        self.assertEqual(client.api_requests_count, 2)

    def test_client_init_custom_model_and_env(self):
        with patch.dict(os.environ, {"NVIDIA_MODEL": "nvidia/nemotron-custom"}, clear=True):
            client = NemotronClient(api_key="custom-key")
            self.assertEqual(client.api_key, "custom-key")
            self.assertEqual(client.model, "nvidia/nemotron-custom")

    @patch("urllib.request.urlopen")
    def test_complete_text_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Found 3 promising telegram channels: @test1, @test2, @test3"
                    }
                }
            ]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        client = NemotronClient(api_key="fake-key")
        result = client.complete("Suggest telegram channels for v2ray configs")
        self.assertIn("@test1", result)
        self.assertIn("@test2", result)

    @patch("urllib.request.urlopen")
    def test_complete_json_extraction(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '```json\n{"queries": ["site:t.me vless", "site:t.me vmess"]}\n```'
                    }
                }
            ]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        client = NemotronClient(api_key="fake-key")
        data = client.complete_json("Generate search queries")
        self.assertIsInstance(data, dict)
        self.assertIn("queries", data)
        self.assertEqual(len(data["queries"]), 2)

    @patch("urllib.request.urlopen")
    def test_analyze_image_payload(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "vless://11111111-2222-3333-4444-555555555555@example.com:443?security=reality"
                    }
                }
            ]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        client = NemotronClient(api_key="fake-key")
        result = client.analyze_image(
            image_url="https://example.com/qr.png",
            prompt="Extract proxy URI from this QR code"
        )
        self.assertIn("vless://", result)

    def test_missing_api_key_raises_or_returns_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            client = NemotronClient(api_key=None)
            self.assertFalse(client.is_available())
            self.assertEqual(client.complete("hello"), "")
            self.assertIsNone(client.complete_json("hello"))


class TestDiscoveryStateManager(OfflineDiscoveryTestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.test_state_file = Path(temporary.name) / "state.json"

    def test_load_and_save_state(self):
        manager = DiscoveryStateManager(state_file=str(self.test_state_file))
        state = manager.get_state()
        self.assertEqual(state["visited_channels"], [])
        self.assertEqual(state["candidate_queue"], [])

        # Add visited and candidates
        manager.add_candidates(["@chan1", "@chan2", "https://t.me/s/chan3"])
        manager.record_visited("@chan1", nodes_found=15, is_active=True)
        manager.save()

        # Reload from disk
        manager2 = DiscoveryStateManager(state_file=str(self.test_state_file))
        state2 = manager2.get_state()
        self.assertIn("chan1", state2["visited_channels"])
        self.assertIn("chan2", state2["candidate_queue"])
        self.assertIn("chan3", state2["candidate_queue"])
        self.assertNotIn("chan1", state2["candidate_queue"])  # Already visited

    def test_candidate_queue_priority(self):
        manager = DiscoveryStateManager(state_file=str(self.test_state_file))
        # Adding same candidate multiple times increases cross-reference count / priority
        manager.add_candidates(["chanA", "chanB"])
        manager.add_candidates(["chanB", "chanC"])
        next_batch = manager.pop_next_candidates(limit=2)
        # chanb has 2 mentions, so it should be prioritized first
        self.assertEqual(next_batch[0], "chanb")

    def test_generate_markdown_leaderboard(self):
        manager = DiscoveryStateManager(state_file=str(self.test_state_file))
        manager.record_visited("TopChannel", nodes_found=42, is_active=True, sub_url="https://t.me/s/TopChannel")
        manager.record_visited("DeadChannel", nodes_found=0, is_active=False)
        md = manager.generate_leaderboard_markdown()
        self.assertIn("TopChannel", md)
        self.assertIn("42", md)
        self.assertIn("Autonomous Subscription Discovery Leaderboard", md)


class TestTelegramChannelScraper(OfflineDiscoveryTestCase):
    def test_parse_channel_page_extracts_uris_and_sister_channels(self):
        sample_html = """
        <div class="tgme_channel_info_description">Official proxy configs. Sister channel: @SisterProxy and join https://t.me/AnotherV2ray. Sub: https://raw.githubusercontent.com/user/repo/main/sub.txt</div>
        <div class="tgme_widget_message" data-post="sample_chan/100">
            <div class="tgme_widget_message_text">
                New high-speed nodes:<br>
                vless://11111111-2222-3333-4444-555555555555@example.com:443?type=tcp&security=reality#US-Fast<br>
                vmess://eyJhZGQiOiIxLjEuMS4xIiwicG9ydCI6NDQzLCJpZCI6IjExMTExMTExLTIyMjItMzMzMy00NDQ0LTU1NTU1NTU1NTU1NSIsInBzIjoiRGVtb1ZtZXNzIn0=
                Forwarded from @V2rayBest
            </div>
            <a class="tgme_widget_message_photo_wrap" style="background-image:url('https://cdn.telegram.org/file/qr123.jpg')"></a>
        </div>
        <div class="tgme_widget_message" data-post="sample_chan/99">
            <div class="tgme_widget_message_text">
                Check our second sub: https://sub.v2ray.io/all.txt
            </div>
        </div>
        """
        scraper = TelegramChannelScraper()
        result = scraper.parse_channel_page(sample_html, current_channel="sample_chan")

        # Proxy URIs found
        self.assertEqual(len(result.proxy_uris), 2)
        self.assertTrue(any("vless://" in u for u in result.proxy_uris))
        self.assertTrue(any("vmess://" in u for u in result.proxy_uris))

        # External subscription URLs
        self.assertIn("https://raw.githubusercontent.com/user/repo/main/sub.txt", result.sub_urls)
        self.assertIn("https://sub.v2ray.io/all.txt", result.sub_urls)

        # Discovered sister channels
        self.assertIn("sisterproxy", result.discovered_channels)
        self.assertIn("anotherv2ray", result.discovered_channels)
        self.assertIn("v2raybest", result.discovered_channels)

        # Image URLs
        self.assertIn("https://cdn.telegram.org/file/qr123.jpg", result.image_urls)

        # Pagination ID (min post id is 99)
        self.assertEqual(result.oldest_post_id, 99)


class TestWebSearchScraper(OfflineDiscoveryTestCase):
    def test_parse_duckduckgo_results(self):
        sample_ddg_html = """
        <div class="result results_links results_links_deep web-result">
            <a class="result__url" href="https://t.me/s/v2ray_free_configs">t.me/s/v2ray_free_configs</a>
            <a class="result__snippet">Telegram channel with free vless and vmess configs.</a>
        </div>
        <div class="result results_links results_links_deep web-result">
            <a class="result__url" href="https://github.com/free-v2ray/subscription">github.com/free-v2ray/subscription</a>
            <a class="result__snippet">Raw subscription aggregator updated hourly.</a>
        </div>
        """
        scraper = WebSearchScraper()
        channels, urls = scraper.parse_duckduckgo_html(sample_ddg_html)
        self.assertIn("v2ray_free_configs", channels)
        self.assertTrue(any("github.com/free-v2ray" in u for u in urls))


class TestDiscoveryAgent(OfflineDiscoveryTestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        previous_cwd = Path.cwd()
        # The agent's public-source and OCR paths are cwd-relative.
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous_cwd)
        (self.root / "data").mkdir()
        self.test_state_file = self.root / "data" / "state.json"
        self.test_discovered_file = self.root / "sources.discovered.json"
        self.test_leaderboard_file = self.root / "DISCOVERY.md"
        self.test_sources_file = self.root / "sources.json"
        self.test_sources_file.write_text(json.dumps({
            "sources": [{
                "id": "fixture-public", "name": "Fixture Public",
                "url": "https://example.com/sub.txt", "format": "auto", "enabled": True,
            }]
        }), encoding="utf-8")

    @patch("src.discovery.search_engine.WebSearchScraper.search_github", return_value=[])
    @patch("src.discovery.search_engine.WebSearchScraper.search_duckduckgo", return_value=([], []))
    @patch("src.discovery.search_engine.TelegramChannelScraper.fetch_channel_page")
    def test_discovery_agent_run_cycle(self, mock_fetch, _mock_ddg, _mock_gh):
        # Mock telegram page returning a valid node and a sister channel
        mock_fetch.return_value = """
        <div class="tgme_channel_info_description">Check sister @NewChan</div>
        <div class="tgme_widget_message" data-post="mockchan/1">
            <div class="tgme_widget_message_text">
                vless://11111111-2222-3333-4444-555555555555@1.2.3.4:443?type=tcp&security=reality#MockNode
            </div>
        </div>
        """
        agent = DiscoveryAgent(
            state_file=str(self.test_state_file),
            discovered_sources_file=str(self.test_discovered_file),
            leaderboard_file=str(self.test_leaderboard_file),
            initial_seeds=["mockchan"],
            nemotron_client=NemotronClient(api_key=""),
        )

        discovered = agent.run_cycle(max_channels=1, max_time_seconds=10)
        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0]["id"], "tg-mockchan")
        self.assertTrue(self.test_discovered_file.exists())
        self.assertTrue(self.test_leaderboard_file.exists())

        # Verify sister channel @NewChan got added to candidate queue
        state = agent.state_manager.get_state()
        self.assertIn("newchan", state["candidate_queue"])
        self.assertIn("mockchan", state["visited_channels"])

    def test_load_sources_merges_discovered_sources(self):
        # Write dummy discovered sources
        self.test_discovered_file.write_text(json.dumps({
            "sources": [
                {
                    "id": "tg-autodiscovered",
                    "name": "Auto Discovered Telegram",
                    "url": "https://t.me/s/autodiscovered",
                    "format": "telegram_web",
                    "tags": ["auto-discovered", "telegram"],
                    "enabled": True
                }
            ]
        }, indent=2), encoding="utf-8")

        sources = load_sources(
            sources_path=str(self.test_sources_file),
            discovered_sources_path=str(self.test_discovered_file),
        )
        discovered_item = next((s for s in sources if s.id == "tg-autodiscovered"), None)
        self.assertIsNotNone(discovered_item)
        self.assertIn("auto-discovered", discovered_item.tags)

    def test_record_external_source_in_leaderboard(self):
        manager = DiscoveryStateManager(state_file=str(self.test_state_file))
        manager.record_external_source("gh-1234", "gh:user/repo", 120, "https://raw.githubusercontent.com/user/repo/main/sub.txt")
        md = manager.generate_leaderboard_markdown()
        self.assertIn("`gh:user/repo`", md)
        self.assertIn("120", md)

    def test_load_local_dotenv(self):
        from src.discovery.agent import load_local_dotenv
        temp_env = self.root / "data" / "test_dot_env"
        temp_env.write_text('TEST_NVAPI_DUMMY="nvapi-123456"\n# comment\n', encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            load_local_dotenv(str(temp_env))
            self.assertEqual(os.environ.get("TEST_NVAPI_DUMMY"), "nvapi-123456")

    def test_state_filters_spam_and_tracks_api_usage(self):
        manager = DiscoveryStateManager(state_file=str(self.test_state_file))
        manager.add_candidates(["stickers", "cuddlyturtler2_2026", "vless_free_iran", "randomchat"])
        queue = manager.get_state()["candidate_queue"]
        self.assertNotIn("stickers", queue)
        self.assertNotIn("cuddlyturtler2_2026", queue)
        self.assertEqual(queue[0], "vless_free_iran")

        manager.record_visited("vless_free_iran", nodes_found=25, is_active=True, sub_url="https://t.me/s/vless_free_iran")
        manager.record_api_usage(500)
        md = manager.generate_leaderboard_markdown()
        self.assertIn("**500**", md)
        self.assertEqual(manager.get_oldest_active_channels(limit=1), ["vless_free_iran"])

    @patch("urllib.request.urlopen")
    def test_download_image_as_data_uri_base64(self, mock_urlopen):
        from src.discovery.nemotron_client import _download_image_as_data_uri
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"\x89PNG\r\n\x1a\nFAKE_PNG_DATA"
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        uri = _download_image_as_data_uri("https://cdn.telegram.org/file/test.png")
        self.assertTrue(uri.startswith("data:image/png;base64,"))

    @patch("src.discovery.search_engine.WebSearchScraper.search_github_readmes", return_value=[])
    @patch("src.discovery.search_engine.WebSearchScraper.search_github", return_value=[])
    @patch("src.discovery.search_engine.WebSearchScraper.search_duckduckgo", return_value=([], []))
    @patch("src.discovery.search_engine.TelegramChannelScraper.fetch_channel_page")
    @patch("src.discovery.nemotron_client.NemotronClient.complete_json")
    def test_extended_mode_respects_max_api_requests(
        self, mock_complete_json, mock_tg_fetch, _mock_ddg, _mock_gh, _mock_readmes
    ):
        def fake_json(*args, **kwargs):
            client.api_requests_count += 1
            return {"web_queries": ["site:t.me vless"], "github_queries": []}

        mock_complete_json.side_effect = fake_json
        mock_tg_fetch.return_value = """
        <div class="tgme_widget_message" data-post="mockext/1">
            <div class="tgme_widget_message_text">
                vless://11111111-2222-3333-4444-555555555555@1.2.3.4:443?security=reality#ExtNode
            </div>
        </div>
        """

        client = NemotronClient(api_key="fake-nvapi-key")
        agent = DiscoveryAgent(
            state_file=str(self.test_state_file),
            discovered_sources_file=str(self.test_discovered_file),
            leaderboard_file=str(self.test_leaderboard_file),
            initial_seeds=["mockext"],
            nemotron_client=client,
        )
        discovered = agent.run_cycle(max_channels=50, max_time_seconds=30, max_api_requests=3)
        self.assertGreaterEqual(client.api_requests_count, 1)
        self.assertLessEqual(client.api_requests_count, 4)
        self.assertEqual(len(discovered), 1)

    def test_incremental_state_persists_across_runs(self):
        manager = DiscoveryStateManager(state_file=str(self.test_state_file))
        manager.record_visited("active_tg", nodes_found=50, is_active=True, oldest_post_id=420)
        manager.record_visited_url("https://example.com/readme.md")
        manager.record_rejected_channels(["spam_bot"])
        manager.save()

        # Reload on next run
        reloaded = DiscoveryStateManager(state_file=str(self.test_state_file))
        self.assertTrue(reloaded.is_url_visited("https://example.com/readme.md"))
        self.assertEqual(reloaded.get_channel_oldest_post_id("active_tg"), 420)
        self.assertEqual(reloaded.get_top_active_handles(limit=1), ["active_tg"])
        reloaded.add_candidates(["spam_bot", "new_vless_sub"])
        self.assertNotIn("spam_bot", reloaded.get_state()["candidate_queue"])
        self.assertIn("new_vless_sub", reloaded.get_state()["candidate_queue"])


class TestDiscoveryFixtureSafety(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        previous_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous_cwd)
        (self.root / "data").mkdir()

    def test_ambient_api_key_never_causes_network(self):
        case = TestDiscoveryAgent("test_discovery_agent_run_cycle")
        self.addCleanup(case.doCleanups)
        case.setUp()
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "synthetic-ambient-key"}, clear=True), patch(
            "urllib.request.urlopen"
        ) as transport, patch(
            "urllib.request.OpenerDirector.open", side_effect=AssertionError("unexpected direct request")
        ) as direct_transport:
            response = transport.return_value.__enter__.return_value
            response.read.return_value = b'{"choices":[{"message":{"role":"assistant","content":"{}"}}]}'
            case.test_discovery_agent_run_cycle()
            self.assertEqual(transport.call_count + direct_transport.call_count, 0)

    def test_fixtures_leave_existing_files_unchanged(self):
        sentinels = {
            "data/test_discovery_state.json": b"state sentinel\x00",
            "data/test_agent_state.json": b"agent sentinel\x00",
            "test_sources.discovered.json": b"sources sentinel\x00",
            "test_DISCOVERY.md": b"leaderboard sentinel\x00",
            "data/test_dot_env": b"dotenv fixture sentinel\x00",
            "data/discovered_ocr_configs.txt": b"vless://fixture-uuid@192.0.2.10:443?security=tls#Private-sentinel\n",
            "sources.json": b'{"sources": []}',
            ".env": b"TEST_NVAPI_DUMMY=existing-user-value\n",
        }
        for name, content in sentinels.items():
            (self.root / name).write_bytes(content)
        suite = unittest.TestSuite([
            TestDiscoveryStateManager("test_load_and_save_state"),
            TestDiscoveryAgent("test_discovery_agent_run_cycle"),
            TestDiscoveryAgent("test_load_local_dotenv"),
        ])
        result = unittest.TestResult()
        suite.run(result)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        for name, content in sentinels.items():
            with self.subTest(path=name):
                path = self.root / name
                self.assertTrue(path.is_file(), "fixture deleted an existing file")
                self.assertEqual(path.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
