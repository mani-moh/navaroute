import base64
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from src.discovery.agent import DiscoveryAgent, load_local_dotenv
from src.discovery.nemotron_client import DiscoveryBudgetExceeded, NemotronClient, _download_image_as_data_uri
from src.discovery.search_engine import TelegramChannelScraper, WebSearchScraper
from src.discovery.state_manager import DiscoveryStateManager, normalize_channel_handle
from src.fetcher import fetch_source, load_sources
from src.network import NetworkError
from src.parsers import parse_proxy_uri

URI = "vless://11111111-2222-3333-4444-555555555555@example.com:443?security=tls&type=ws&path=%2Ffirst&path=%2Fsecond#Public"
PAGE = '<div data-post="mockchan/1">' + URI + ' sister @NewChan</div>'
IMAGE = b"\x89PNG\r\n\x1a\nPUBLIC_SYNTHETIC_IMAGE"


def completion(text):
    return 200, json.dumps({"choices": [{"message": {"role": "assistant", "content": text}}]}).encode()


class OfflineDiscoveryTestCase(unittest.TestCase):
    def setUp(self):
        # Windows TLS needs SystemRoot; never inherit credentials or proxy settings.
        safe_env = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "TEMP", "TMP") if k in os.environ}
        environment = patch.dict(os.environ, safe_env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        for target in ("urllib.request.urlopen", "urllib.request.OpenerDirector.open", "src.network._resolve"):
            guard = patch(target, side_effect=AssertionError("unexpected external request"))
            transport = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(transport.assert_not_called)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        previous_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous_cwd)
        (self.root / "data").mkdir()
        # Expected synthetic failure warnings belong in assertions, not suite output.
        quiet = patch("src.discovery.nemotron_client.logger")
        quiet.start()
        self.addCleanup(quiet.stop)


class TestNemotronClient(OfflineDiscoveryTestCase):
    def client(self, limit=500, seconds=30):
        client = NemotronClient(api_key="synthetic-key")
        client.configure_cycle(max_attempts=limit, deadline=seconds)
        return client

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes", create=True)
    def test_text_json_and_fallback_use_safe_counted_transport(self, transport, _clock):
        transport.side_effect = [(404, b"secret-response"), completion('```json\n{"channels": ["proxychan"]}\n```')]
        client = self.client()
        self.assertEqual(client.complete_json("public query"), {"channels": ["proxychan"]})
        self.assertEqual(client.api_requests_count, 2)
        for args, kwargs in transport.call_args_list:
            self.assertEqual(args[0], "https://integrate.api.nvidia.com/v1/chat/completions")
            self.assertTrue(kwargs["authenticated"])
            self.assertEqual(kwargs["max_bytes"], 2 * 1024 * 1024)
            self.assertEqual(kwargs["deadline"], 30)

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes", create=True, return_value=(503, b"secret"))
    def test_real_retry_loop_one_remaining_attempt(self, transport, sleep, _clock):
        client = self.client(limit=1)
        with self.assertRaises(RuntimeError):
            client.complete("hello")
        self.assertEqual(client.api_requests_count, 1)
        self.assertEqual(transport.call_count, 1)
        sleep.assert_not_called()

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes", create=True, side_effect=NetworkError("secret-proxy-error"))
    def test_real_retry_loop_499_stops_at_hard_500(self, transport, sleep, _clock):
        client = self.client()
        client.api_requests_count = 499
        client._cycle_start_requests = 0
        with self.assertRaises(RuntimeError):
            client.complete("hello")
        self.assertEqual(client.api_requests_count, 500)
        self.assertEqual(transport.call_count, 1)
        sleep.assert_not_called()

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes", create=True, return_value=(429, b"private-error"))
    def test_backoff_expiry_stops_before_sleep_or_retry(self, transport, sleep, _clock):
        client = self.client(seconds=1)
        with self.assertRaises(RuntimeError):
            client.complete("public prompt")
        self.assertEqual(client.api_requests_count, 1)
        sleep.assert_not_called()
        self.assertLessEqual(transport.call_args.kwargs["timeout"], 1)

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes", create=True)
    def test_retry_success_and_fresh_cycle_budget(self, transport, sleep, _clock):
        transport.side_effect = [(503, b""), completion("worked"), completion("next")]
        client = self.client(limit=2)
        self.assertEqual(client.complete("hello"), "worked")
        client.configure_cycle(max_attempts=1, deadline=30)
        self.assertEqual(client.complete("next cycle"), "next")
        self.assertEqual(client.api_requests_count, 3)
        sleep.assert_called_once_with(2)

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes", create=True, side_effect=NetworkError("SECRET"))
    def test_errors_and_invalid_json_never_log_response_or_prompt(self, transport, _sleep, _clock):
        client = self.client(limit=2)
        import logging
        with patch("src.discovery.nemotron_client.logger", logging.getLogger("src.discovery.nemotron_client")), self.assertLogs("src.discovery.nemotron_client", level="WARNING") as logs:
            with self.assertRaises(RuntimeError):
                client.complete("SECRET prompt")
        self.assertNotIn("SECRET", "\n".join(logs.output))
        client.configure_cycle(max_attempts=1, deadline=30)
        transport.side_effect = None
        transport.return_value = completion("SECRET invalid JSON")
        with patch("src.discovery.nemotron_client.logger", logging.getLogger("src.discovery.nemotron_client")), self.assertLogs("src.discovery.nemotron_client", level="WARNING") as logs:
            self.assertIsNone(client.complete_json("SECRET"))
        self.assertNotIn("SECRET", "\n".join(logs.output))

    @patch("src.discovery.nemotron_client.fetch_bytes", create=True)
    def test_unapproved_api_origin_rejected_before_request(self, transport):
        for base in ("http://integrate.api.nvidia.com/v1", "https://attacker.example/v1", "https://u:SECRET@integrate.api.nvidia.com/v1"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                NemotronClient(api_key="synthetic", base_url=base).complete("public")
        transport.assert_not_called()

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes", create=True)
    def test_image_reuse_only_after_success_and_never_send_unchecked_url(self, transport, _clock):
        client = self.client(limit=4)
        transport.side_effect = [(200, IMAGE), completion(URI), (200, IMAGE)]
        self.assertIn("vless://", client.analyze_image("https://cdn.telegram.org/public.png"))
        self.assertIn("vless://", client.analyze_image("https://cdn.telegram.org/other.png"))
        self.assertEqual(client.api_requests_count, 1)
        payload = json.loads(transport.call_args_list[1].kwargs["data"])
        image = payload["messages"][1]["content"][1]["image_url"]["url"]
        self.assertEqual(image, "data:image/png;base64," + base64.b64encode(IMAGE).decode())
        transport.side_effect = NetworkError("download failed")
        self.assertEqual(client.analyze_image("https://cdn.telegram.org/failure.png"), "")
        self.assertEqual(client.api_requests_count, 1)

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes", create=True)
    def test_image_failed_analysis_is_retryable(self, transport, _clock):
        client = self.client(limit=2)
        transport.side_effect = [(200, IMAGE), completion(""), (200, IMAGE), completion(URI)]
        self.assertEqual(client.analyze_image("https://cdn.telegram.org/public.png"), "")
        self.assertIn("vless://", client.analyze_image("https://cdn.telegram.org/public.png"))
        self.assertEqual(client.api_requests_count, 2)
        self.assertLessEqual(len(client.image_results), 400)

    @patch("src.discovery.nemotron_client.fetch_bytes", create=True, return_value=(200, b"not an image"))
    def test_unknown_image_format_is_not_forwarded(self, transport):
        self.assertEqual(_download_image_as_data_uri("https://cdn.telegram.org/public.png"), "")
        self.assertEqual(transport.call_args.kwargs["max_bytes"], 2 * 1024 * 1024)

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes", create=True, return_value=completion("success"))
    def test_pacing_expiry_after_success_is_normal_budget_stop(self, transport, sleep, _clock):
        client = self.client(seconds=1)
        client.min_request_interval = 2
        self.assertEqual(client.complete("first"), "success")
        with self.assertRaises(DiscoveryBudgetExceeded):
            client.complete("second")
        self.assertEqual(client.api_requests_count, 1)
        sleep.assert_not_called()

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes", create=True)
    def test_successful_no_configs_image_remembered_without_raw_response(self, transport, _clock):
        client = self.client(limit=2)
        transport.side_effect = [(200, IMAGE), completion("No configs in PUBLIC image"), (200, IMAGE)]
        client.analyze_image("https://cdn.telegram.org/public.png")
        self.assertEqual(client.analyze_image("https://cdn.telegram.org/public.png"), "")
        manager = DiscoveryStateManager(str(self.root / "state.json"))
        manager.state["image_results"] = client.image_results
        manager.save()
        self.assertEqual(client.api_requests_count, 1)
        self.assertNotIn("No configs", (self.root / "state.json").read_text())

    def test_missing_key_is_offline(self):
        client = NemotronClient(api_key="")
        self.assertFalse(client.is_available())
        self.assertEqual(client.complete("hello"), "")

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes", create=True, return_value=completion("last permitted"))
    def test_explicit_positive_budget_is_exact_not_default_quick_ceiling(self, transport, _clock):
        client = self.client(limit=501)
        client.api_requests_count = 500
        self.assertEqual(client.complete("last permitted"), "last permitted")
        self.assertEqual(client.api_requests_count, 501)
        with self.assertRaises(DiscoveryBudgetExceeded):
            client.complete("over cap")
        self.assertEqual(transport.call_count, 1)


class TestDiscoveryStateManager(OfflineDiscoveryTestCase):
    def test_valid_state_priority_and_roundtrip(self):
        path = self.root / "state.json"
        manager = DiscoveryStateManager(str(path))
        manager.add_candidates(["@chanA", "chanB", "https://t.me/s/chanC"])
        manager.add_candidates(["chanB"])
        self.assertEqual(manager.pop_next_candidates(1), ["chanb"])
        manager.record_visited("chana", 3, True, oldest_post_id=100)
        manager.record_visited_url("https://example.com/readme.md")
        manager.save()
        loaded = DiscoveryStateManager(str(path))
        self.assertEqual(loaded.get_channel_oldest_post_id("chana"), 100)
        self.assertEqual(loaded.get_top_active_handles(), ["chana"])
        self.assertTrue(loaded.is_url_visited("https://example.com/readme.md"))

    def test_complete_handle_validation(self):
        for raw in ("abc?SECRET=1", "abc garbage", "https://evil.example/chan", "https://u:SECRET@t.me/chan", "a" * 33, "https://t.me/chan?token=SECRET"):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_channel_handle(raw), "")
        self.assertEqual(normalize_channel_handle("https://t.me/s/ProxyChan/123"), "proxychan")

    def test_corrupt_state_preserved_and_rejected(self):
        path = self.root / "state.json"
        for data in ("{bad", '[]', '{"candidate_queue":"not a list"}', '{"candidate_scores":{"chan":true}}', '{"sources_metadata":{"chan":{"is_active":"yes"}}}', '{"sources_metadata":{"chan":{}}}', '{"visited_urls":["https://u:SECRET@example.com/sub"]}', '{"candidate_queue":["chan?SECRET"]}', '{"raw_response":"SECRET"}'):
            with self.subTest(data=data):
                path.write_text(data, encoding="utf-8")
                with self.assertRaises(ValueError):
                    DiscoveryStateManager(str(path))
                self.assertEqual(path.read_text(encoding="utf-8"), data)

    def test_failed_replace_preserves_old_state_and_propagates(self):
        path = self.root / "state.json"
        manager = DiscoveryStateManager(str(path))
        manager.add_candidates(["proxychan"])
        manager.save()
        old = path.read_bytes()
        with patch("pathlib.Path.replace", side_effect=OSError("synthetic failure")), self.assertRaises(OSError):
            manager.save()
        self.assertEqual(path.read_bytes(), old)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_pagination_can_wrap(self):
        manager = DiscoveryStateManager(str(self.root / "state.json"))
        for post in (90, 1, 100):
            manager.record_visited("proxychan", 5, True, oldest_post_id=post)
        self.assertEqual(manager.get_channel_oldest_post_id("proxychan"), 100)

    def test_loaded_metadata_requires_complete_matching_telegram_handle(self):
        path = self.root / "state.json"
        manager = DiscoveryStateManager(str(path))
        manager.record_visited("proxychan", 1, True)
        manager.state["sources_metadata"]["proxychan"]["handle"] = "proxychan/extra"
        raw = json.dumps(manager.state)
        path.write_text(raw, encoding="utf-8")
        with self.assertRaises(ValueError):
            DiscoveryStateManager(str(path))
        self.assertEqual(path.read_text(), raw)


class TestScrapers(OfflineDiscoveryTestCase):
    def test_full_links_handles_and_duplicate_readme_links(self):
        result = TelegramChannelScraper().parse_channel_page('<div data-post="proxychan/100">' + URI + ' https://example.com/sub.txt?token=SECRET https://raw.githubusercontent.com/user/repo/main/sub.txt @' + 'a' * 33 + ' @SisterProxy</div>')
        self.assertEqual(result.oldest_post_id, 100)
        self.assertEqual(result.discovered_channels, ["sisterproxy"])
        self.assertEqual(result.sub_urls, ["https://raw.githubusercontent.com/user/repo/main/sub.txt"])
        self.assertEqual(result.proxy_uris, [URI])
        channels, urls = WebSearchScraper().parse_duckduckgo_html('<a href="https://u:SECRET@example.com/sub.txt">x</a><a href="https://127.0.0.1/sub.txt">x</a><a href="https://github.com/public/repo">x</a><a href="https://github.com/public/repo">x</a><a href="https://t.me/s/SisterProxy">x</a>')
        self.assertEqual(channels, ["sisterproxy"])
        self.assertEqual(urls, ["https://github.com/public/repo"])

    @patch("src.discovery.search_engine.fetch_bytes", create=True, return_value=(200, b"public"))
    def test_scraper_deadline_and_cap_reach_transport(self, transport):
        scraper = TelegramChannelScraper()
        scraper.deadline = 5
        self.assertEqual(scraper.fetch_channel_page("proxychan"), "public")
        self.assertEqual(transport.call_args.kwargs["deadline"], 5)
        self.assertEqual(transport.call_args.kwargs["max_bytes"], 32 * 1024 * 1024)

    @patch("src.discovery.search_engine.fetch_bytes", create=True)
    def test_retryable_vs_confirmed_dead(self, transport):
        scraper = TelegramChannelScraper()
        transport.side_effect = NetworkError("SECRET")
        self.assertIsNone(scraper.fetch_channel_page("proxychan"))
        self.assertEqual(scraper.last_outcome, "retryable")
        transport.side_effect = None
        transport.return_value = (404, b"SECRET")
        self.assertIsNone(scraper.fetch_channel_page("proxychan"))
        self.assertEqual(scraper.last_outcome, "dead")

    @patch("src.discovery.search_engine.fetch_bytes", create=True, return_value=(200, b'{"items":[]}'))
    def test_public_github_search_never_uses_ambient_credentials(self, transport):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "SECRET"}):
            self.assertEqual(WebSearchScraper().search_github("public"), [])
        self.assertNotIn("Authorization", transport.call_args.kwargs["headers"])


class TestDiscoveryAgent(OfflineDiscoveryTestCase):
    def agent(self, seeds=None, key=""):
        return DiscoveryAgent(state_file=str(self.root / "data/state.json"), discovered_sources_file=str(self.root / "sources.discovered.json"), leaderboard_file=str(self.root / "DISCOVERY.md"), initial_seeds=seeds or [], nemotron_client=NemotronClient(api_key=key))

    @contextmanager
    def crawl(self, agent, pages=PAGE):
        with patch.object(agent, "_expand_queries_via_nemotron"), patch.object(agent.tg_scraper, "fetch_channel_page", side_effect=pages if isinstance(pages, list) else None, return_value=pages) as fetch:
            yield fetch

    def write_public(self, sources=None):
        path = self.root / "sources.json"
        path.write_text(json.dumps({"sources": sources or []}), encoding="utf-8")
        return str(path)

    def test_cycle_retains_deep_configs_and_aggregation_sees_complete_semantics(self):
        agent = self.agent(["proxychan"])
        with self.crawl(agent, ['<div data-post="proxychan/100">none</div>', '<div data-post="proxychan/90">' + URI + '</div>']):
            sources = agent.run_cycle(max_channels=1)
        self.assertEqual({s["id"] for s in sources}, {"tg-proxychan", "nemotron-vision-ocr"})
        cache = next(s for s in load_sources(discovered_sources_path="sources.discovered.json", sources_path=self.write_public()) if s.url == "data/discovered_ocr_configs.txt")
        nodes, _, stats = fetch_source(cache)
        self.assertTrue(stats.success)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].params["path"], ["/first", "/second"])
        self.assertIn("retained", cache.name.lower())

    def test_zero_limits_do_no_work_negative_invalid_default_quick_cap(self):
        agent = self.agent(["proxychan"])
        with self.crawl(agent) as fetch:
            agent.run_cycle(max_channels=0)
            agent.run_cycle(max_time_seconds=0)
            fetch.assert_not_called()
        for kwargs in ({"max_channels": -1}, {"max_time_seconds": -1}, {"max_api_requests": -1}):
            with self.assertRaises(ValueError):
                agent.run_cycle(**kwargs)
        with self.crawl(agent):
            agent.run_cycle(max_channels=1, max_api_requests=0)
        self.assertEqual(agent.nemotron._max_attempts, 500)

    def test_extended_mode_still_obeys_channel_cap_and_no_budget_exhaustion_calls(self):
        agent = self.agent(["proxyone", "proxytwo"], key="synthetic")
        with self.crawl(agent) as fetch:
            agent.run_cycle(max_channels=1, max_api_requests=500)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(agent.nemotron.api_requests_count, 0)
        self.assertIn("proxytwo", agent.state_manager.state["candidate_queue"])

    @patch("src.discovery.agent.time.monotonic")
    def test_deadline_covers_partial_dequeue(self, clock):
        clock.return_value = 0
        agent = self.agent(["proxyone", "proxytwo", "proxythree"])
        def page(*args, **kwargs):
            clock.return_value = 10
            return PAGE
        with self.crawl(agent) as fetch:
            fetch.side_effect = page
            agent.run_cycle(max_channels=3, max_time_seconds=10)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(agent.state_manager.state["candidate_queue"], ["proxyone", "proxytwo", "proxythree"])
        self.assertNotIn("proxyone", agent.state_manager.state["visited_channels"])

    def test_exception_after_dequeue_restores_unfinished_suffix(self):
        agent = self.agent(["proxyone", "proxytwo", "proxythree"])
        with self.crawl(agent, [PAGE, RuntimeError("synthetic cancellation")]), self.assertRaises(RuntimeError):
            agent.run_cycle(max_channels=3)
        expected = ["proxytwo", "proxythree", "newchan"]
        self.assertEqual(agent.state_manager.state["candidate_queue"], expected)
        self.assertIn("proxyone", agent.state_manager.state["visited_channels"])
        self.assertEqual(json.loads((self.root / "data/state.json").read_text())["candidate_queue"], expected)

    def test_timeout_then_success_across_cycles_and_confirmed_death(self):
        agent = self.agent(["proxychan"])
        with self.crawl(agent, None):
            agent.run_cycle(max_channels=1)
        self.assertNotIn("proxychan", agent.state_manager.state["visited_channels"])
        self.assertIn("proxychan", agent.state_manager.state["candidate_queue"])
        with self.crawl(agent):
            agent.run_cycle(max_channels=1)
        self.assertIn("proxychan", agent.state_manager.state["visited_channels"])
        agent.tg_scraper.last_outcome = "dead"
        agent.state_manager.restore_candidates(["proxychan"])
        with self.crawl(agent, None):
            sources = agent.run_cycle(max_channels=1)
        self.assertNotIn("tg-proxychan", {s["id"] for s in sources})
        self.assertFalse(agent.state_manager.state["sources_metadata"]["proxychan"]["is_active"])

    def test_corrupt_registries_or_cache_are_not_overwritten(self):
        for filename, content in (("sources.discovered.json", '{"sources":"bad"}'), ("sources.json", '{bad'), ("sources.discovered.json", '{"sources":[{"id":"x","url":"https://u:SECRET@example.com/sub"}]}'), ("data/discovered_ocr_configs.txt", "not a valid retained config")):
            with self.subTest(filename=filename):
                path = self.root / filename
                path.write_text(content, encoding="utf-8")
                agent = self.agent()
                with self.crawl(agent), self.assertRaises(ValueError):
                    agent.run_cycle(max_channels=0)
                self.assertEqual(path.read_text(encoding="utf-8"), content)
                path.unlink()

    def test_checkpoint_failure_propagates_preserving_old_output(self):
        agent = self.agent(["proxychan"])
        registry = self.root / "sources.discovered.json"
        registry.write_text('{"sources": []}', encoding="utf-8")
        with self.crawl(agent), patch("src.discovery.agent.atomic_write_text", create=True, side_effect=OSError("synthetic failure")), self.assertRaises(OSError):
            agent.run_cycle(max_channels=1)
        self.assertEqual(registry.read_text(encoding="utf-8"), '{"sources": []}')

    def test_unsafe_urls_never_probed_or_persisted_and_new_urls_deduplicate(self):
        agent = self.agent(["proxychan"])
        page = PAGE + ' https://u:SECRET@example.com/sub.txt https://127.0.0.1/sub.txt https://example.com/sub.txt?token=SECRET https://raw.githubusercontent.com/user/repo/main/sub.txt https://raw.githubusercontent.com/user/repo/main/sub.txt'
        with self.crawl(agent, page), patch("src.discovery.agent.fetch_bytes", create=True, return_value=(200, URI.encode())) as probe:
            agent.run_cycle(max_channels=1)
        self.assertEqual(probe.call_count, 1)
        for path in (self.root / "data/state.json", self.root / "sources.discovered.json", self.root / "DISCOVERY.md"):
            self.assertNotIn("SECRET", path.read_text(encoding="utf-8"))
            self.assertNotIn("127.0.0.1", path.read_text(encoding="utf-8"))

    def test_existing_and_curated_urls_deduplicate(self):
        row = {"id": "public", "name": "Public", "url": "https://t.me/s/proxychan", "format": "telegram_web"}
        self.write_public([row])
        (self.root / "sources.discovered.json").write_text(json.dumps({"sources": [dict(row, id="duplicate"), dict(row, id="other")]}), encoding="utf-8")
        agent = self.agent(["proxychan"])
        with self.crawl(agent):
            sources = agent.run_cycle(max_channels=1)
        self.assertEqual(sum(s["url"] == row["url"] for s in sources), 0)

    @patch("src.discovery.agent.time.monotonic")
    @patch("src.discovery.search_engine.fetch_bytes", create=True)
    def test_expansion_stops_when_search_consumes_deadline(self, transport, clock):
        clock.return_value = 0
        def search(*args, **kwargs):
            clock.return_value = 10
            return 200, b'<a href="https://raw.githubusercontent.com/user/repo/main/sub.txt">public</a>'
        transport.side_effect = search
        agent = self.agent(["proxychan"])
        sources = agent.run_cycle(max_time_seconds=10)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(sources, [])
        self.assertEqual(transport.call_args.kwargs["deadline"], 10)

    def test_retryable_probe_is_not_marked_visited(self):
        agent = self.agent(["proxychan"])
        page = PAGE + ' https://raw.githubusercontent.com/user/repo/main/sub.txt'
        with self.crawl(agent, page), patch("src.discovery.agent.fetch_bytes", create=True, side_effect=NetworkError("synthetic")):
            agent.run_cycle(max_channels=1)
        self.assertFalse(agent.state_manager.is_url_visited("https://raw.githubusercontent.com/user/repo/main/sub.txt"))

    def test_dotenv_fixture_is_explicit_and_does_not_override(self):
        path = self.root / "synthetic.env"
        path.write_text('TEST_DUMMY="synthetic"\n', encoding="utf-8")
        load_local_dotenv(str(path))
        self.assertEqual(os.environ["TEST_DUMMY"], "synthetic")

    @patch("src.discovery.search_engine.fetch_bytes", create=True)
    def test_webpage_retains_full_config_not_truncated_prompt_snippet(self, transport):
        agent = self.agent()
        # The snippet cuts a valid URI before its second path, but retention must not.
        padding = "public text " * 373
        transport.return_value = (200, (padding + URI).encode())
        discovered = {}
        agent._analyze_webpage_via_nemotron("https://github.com/public/repo", discovered, set())
        self.assertEqual(len(agent._retained), 1)
        node = parse_proxy_uri(next(iter(agent._retained.values())))
        self.assertEqual(node.params["path"], ["/first", "/second"])

    def test_existing_source_confirmed_dead_and_retryable_retained(self):
        row = {"id": "sub-old", "name": "Public", "url": "https://example.com/sub.txt"}
        path = self.root / "sources.discovered.json"
        path.write_text(json.dumps({"sources": [row]}), encoding="utf-8")
        agent = self.agent()
        with self.crawl(agent), patch("src.discovery.agent.fetch_bytes", side_effect=NetworkError("synthetic")):
            self.assertEqual([s["id"] for s in agent.run_cycle(max_channels=1)], ["sub-old"])
        with self.crawl(agent), patch("src.discovery.agent.fetch_bytes", return_value=(410, b"")):
            self.assertEqual(agent.run_cycle(max_channels=1), [])

    def test_checkpoint_state_failure_never_returns_success(self):
        agent = self.agent(["proxychan"])
        with self.crawl(agent), patch.object(agent.state_manager, "save", side_effect=OSError("synthetic failure")), self.assertRaises(OSError):
            agent.run_cycle(max_channels=1)

    @patch("src.discovery.agent.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes", create=True, return_value=(503, b"SECRET"))
    def test_initial_expansion_real_api_failure_propagates_and_stays_counted(self, transport, _clock):
        agent = self.agent(["proxychan"], key="synthetic")
        with self.assertRaises(RuntimeError):
            agent.run_cycle(max_api_requests=1)
        self.assertEqual(agent.nemotron.api_requests_count, 1)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(agent.state_manager.state["candidate_queue"], ["proxychan"])

    def test_retained_cache_and_image_records_are_bounded(self):
        agent = self.agent()
        text = "\n".join(URI.replace("example.com", f"public{i}.example.com") for i in range(401))
        refs = agent._retain(text, "https://t.me/s/proxychan")
        self.assertEqual(len(refs), 400)
        self.assertEqual(len(agent._retained), 400)
        agent._save_checkpoint({}, [])
        self.assertEqual(len((self.root / "data/discovered_ocr_configs.txt").read_text().splitlines()), 400)

    def test_pagination_history_advances_then_wraps_latest(self):
        agent = self.agent(["proxychan"])
        pages = ['<div data-post="proxychan/100">' + URI + '</div>', '<div data-post="proxychan/90">' + URI + '</div>']
        with self.crawl(agent, pages) as fetch:
            agent.run_cycle(max_channels=1)
        self.assertEqual([call.kwargs["before_id"] for call in fetch.call_args_list], [None, 100])
        with self.crawl(agent, ['<div data-post="proxychan/80">' + URI + '</div>', '<div data-post="proxychan/1">' + URI + '</div>']) as fetch:
            agent.run_cycle(max_channels=1)
        self.assertEqual([call.kwargs["before_id"] for call in fetch.call_args_list], [90, 80])
        with self.crawl(agent) as fetch:
            agent.run_cycle(max_channels=1)
        self.assertIsNone(fetch.call_args.kwargs["before_id"])

    def test_retryable_deep_page_preserves_candidate_and_cursor(self):
        agent = self.agent(["proxychan", "proxytwo"])
        with self.crawl(agent, ['<div data-post="proxychan/100">' + URI + '</div>', None, PAGE]):
            agent.run_cycle(max_channels=2)
        self.assertIn("proxychan", agent.state_manager.state["candidate_queue"])
        self.assertNotIn("proxychan", agent.state_manager.state["visited_channels"])
        self.assertIsNone(agent.state_manager.get_channel_oldest_post_id("proxychan"))

    def test_private_cache_redirection_rejected_before_read(self):
        agent = self.agent()
        sentinel = self.root / "synthetic-private.txt"
        sentinel.write_text("PRIVATE_SENTINEL", encoding="utf-8")
        with patch("pathlib.Path.resolve", return_value=sentinel), self.assertRaises(ValueError):
            agent.run_cycle(max_channels=0)
        self.assertEqual(sentinel.read_text(), "PRIVATE_SENTINEL")

    def test_conflicting_registry_ids_rejected_without_loss(self):
        path = self.root / "sources.discovered.json"
        data = json.dumps({"sources": [{"id": "same", "url": "https://example.com/one.txt"}, {"id": "same", "url": "https://example.com/two.txt"}]})
        path.write_text(data, encoding="utf-8")
        agent = self.agent()
        with self.crawl(agent), self.assertRaises(ValueError):
            agent.run_cycle(max_channels=0)
        self.assertEqual(path.read_text(), data)

    def test_telegram_case_and_preview_aliases_deduplicate(self):
        self.write_public([{"id": "public", "url": "https://www.t.me/ProxyChan"}])
        agent = self.agent(["proxychan"])
        with self.crawl(agent):
            sources = agent.run_cycle(max_channels=1)
        self.assertNotIn("tg-proxychan", {source["id"] for source in sources})

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes", create=True)
    def test_image_success_cache_reused_across_cycles(self, transport, _clock):
        agent = self.agent(["proxychan"], key="synthetic")
        image_page = '<div data-post="proxychan/1"><img src="https://cdn.telegram.org/public.png"></div>'
        transport.side_effect = [(200, IMAGE), completion(URI), (200, IMAGE)]
        with self.crawl(agent, image_page):
            agent.run_cycle(max_channels=1, max_api_requests=3)
        reloaded = self.agent(key="synthetic")
        with self.crawl(reloaded, image_page):
            reloaded.run_cycle(max_channels=1, max_api_requests=3)
        self.assertEqual(agent.nemotron.api_requests_count, 1)
        self.assertEqual(reloaded.nemotron.api_requests_count, 0)
        self.assertEqual(reloaded.state_manager.state["sources_metadata"]["proxychan"]["last_nodes_yield"], 1)

    def test_filter_api_failure_restores_whole_dequeued_batch(self):
        agent = self.agent(["chana", "chanb"], key="synthetic")
        with self.crawl(agent), patch.object(agent.nemotron, "complete_json", side_effect=RuntimeError("synthetic cancellation")), self.assertRaises(RuntimeError):
            agent.run_cycle(max_channels=2, max_api_requests=3)
        self.assertEqual(agent.state_manager.state["candidate_queue"], ["chana", "chanb"])

    def test_ambient_nvidia_key_and_existing_files_are_isolated(self):
        sentinel = self.root / "data/existing-private-fixture.txt"
        sentinel.write_bytes(b"SENTINEL")
        agent = self.agent(["proxychan"])
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "SECRET", "GITHUB_TOKEN": "SECRET"}), self.crawl(agent):
            agent.run_cycle(max_channels=1)
        self.assertEqual(agent.nemotron.api_requests_count, 0)
        self.assertEqual(sentinel.read_bytes(), b"SENTINEL")

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes", create=True)
    def test_retryable_image_download_keeps_channel_for_next_cycle(self, transport, _clock):
        agent = self.agent(["proxychan"], key="synthetic")
        page = '<div data-post="proxychan/1"><img src="https://cdn.telegram.org/public.png"></div>'
        transport.side_effect = NetworkError("synthetic download failure")
        with self.crawl(agent, page):
            agent.run_cycle(max_channels=1, max_api_requests=3)
        self.assertIn("proxychan", agent.state_manager.state["candidate_queue"])
        self.assertNotIn("proxychan", agent.state_manager.state["visited_channels"])
        transport.side_effect = [(200, IMAGE), completion(URI)]
        with self.crawl(agent, page):
            agent.run_cycle(max_channels=1, max_api_requests=3)
        self.assertIn("proxychan", agent.state_manager.state["visited_channels"])

    def test_retryable_subscription_only_channel_is_not_acknowledged(self):
        agent = self.agent(["proxychan"])
        page = '<div data-post="proxychan/1">https://example.com/sub.txt</div>'
        with self.crawl(agent, page), patch("src.discovery.agent.fetch_bytes", side_effect=NetworkError("synthetic")):
            agent.run_cycle(max_channels=1)
        self.assertIn("proxychan", agent.state_manager.state["candidate_queue"])
        self.assertNotIn("proxychan", agent.state_manager.state["visited_channels"])

    def test_existing_external_refresh_rotates_beyond_first_five(self):
        rows = [{"id": f"sub-{i}", "url": f"https://example.com/sub{i}.txt"} for i in range(6)]
        (self.root / "sources.discovered.json").write_text(json.dumps({"sources": rows}), encoding="utf-8")
        agent = self.agent()
        with self.crawl(agent), patch("src.discovery.agent.fetch_bytes", return_value=(200, URI.encode())):
            agent.run_cycle(max_channels=1)
        def response(url, **kwargs):
            return (410, b"") if url.endswith("sub5.txt") else (200, URI.encode())
        with self.crawl(agent), patch("src.discovery.agent.fetch_bytes", side_effect=response):
            sources = agent.run_cycle(max_channels=1)
        self.assertNotIn("sub-5", {s["id"] for s in sources})

    def test_transport_rejected_loaded_source_preserves_registry(self):
        path = self.root / "sources.discovered.json"
        raw = '{"sources":[{"id":"unsafe-dns","url":"https://private-dns.example.com/sub.txt"}]}'
        path.write_text(raw, encoding="utf-8")
        agent = self.agent()
        with self.crawl(agent), patch("src.discovery.agent.fetch_bytes", side_effect=ValueError("Non-public destination rejected")), self.assertRaises(ValueError):
            agent.run_cycle(max_channels=1)
        self.assertEqual(path.read_text(), raw)
        self.assertFalse((self.root / "data/state.json").exists())

    def test_retained_cache_deduplicates_loaded_and_curated_rows(self):
        (self.root / "data/discovered_ocr_configs.txt").write_text(URI + "\n", encoding="utf-8")
        (self.root / "sources.discovered.json").write_text(json.dumps({"sources": [{"id": "existing-cache", "url": "data/discovered_ocr_configs.txt"}]}), encoding="utf-8")
        sources = self.agent().run_cycle(max_channels=0)
        self.assertEqual([s["id"] for s in sources], ["existing-cache"])
        self.assertIn("retained", sources[0]["name"].lower())
        self.write_public([{"id": "public-cache", "url": "data/discovered_ocr_configs.txt"}])
        self.assertEqual(self.agent().run_cycle(max_channels=0), [])


if __name__ == "__main__":
    unittest.main()
