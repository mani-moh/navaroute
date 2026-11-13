import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from src.discovery.agent import DiscoveryAgent, load_local_dotenv
from src.discovery.nemotron_client import DiscoveryBudgetExceeded, NemotronClient, _download_image_as_data_uri
from src.discovery.search_engine import TelegramChannelScraper, WebSearchScraper
from src.discovery.state_manager import (
    DiscoveryStateManager,
    bounded_image_results,
    normalize_channel_handle,
    read_public_text,
    serialize_state,
    validate_state,
)
from src.fetcher import fetch_source, load_sources
from src.network import NetworkError
from src.parsers import parse_proxy_uri
from src.storage import atomic_write_text

URI = "vless://11111111-2222-3333-4444-555555555555@example.com:443?security=tls&type=ws&path=%2Ffirst&alpn=h2&alpn=http%2F1.1#Public"
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

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_review6_unicode_canonical_image_record_fits_and_keeps_whole_uris(self, transport, _clock):
        text = "\n".join(URI.split("?")[0].replace("example.com", f"public{i}.example.com") + "?path=" + "界" * 1600 for i in range(5))
        self.assertLess(len(text.encode()), 65536)
        canonical = [parse_proxy_uri(line).to_uri(include_formatted_remark=False) for line in text.splitlines()]
        self.assertGreater(len("\n".join(canonical).encode()), 65536)
        transport.side_effect = [(200, IMAGE), completion(text)]
        client = self.client()
        client.analyze_image("https://cdn.telegram.org/public.png")
        cached = next(iter(client.image_results.values()))
        self.assertLessEqual(len(cached.encode()), 65536)
        self.assertTrue(cached)
        self.assertTrue(all(line in canonical for line in cached.splitlines()))
        manager = DiscoveryStateManager(str(self.root / "state.json"))
        manager.state["image_results"] = client.image_results
        manager.save()


class TestDiscoveryStateManager(OfflineDiscoveryTestCase):
    def test_valid_state_priority_and_roundtrip(self):
        path = self.root / "state.json"
        manager = DiscoveryStateManager(str(path))
        manager.add_candidates(["@chanA", "chanB", "https://t.me/s/chanC"])
        manager.add_candidates(["chanB"])
        self.assertEqual(manager.pop_next_candidates(1), ["chana"])
        self.assertEqual(manager.state["candidate_queue"], ["chanb", "chanc"])
        self.assertEqual(manager.state["candidate_scores"], {"chana": 1, "chanb": 2, "chanc": 1})
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

    def test_fix2_B_pending_child_schema_rejects_corruption_without_replacing_state(self):
        path = self.root / "state.json"
        parent = "https://github.com/public/repo"
        child = {"kind": "subscription", "url": "https://example.com/sub.txt"}
        valid = {"version": 1, "pending_pages": [parent], "pending_children": {parent: [child]}}
        path.write_text(json.dumps(valid), encoding="utf-8")
        manager = DiscoveryStateManager(str(path))
        manager.save()
        self.assertEqual(DiscoveryStateManager(str(path)).state["pending_children"], {parent: [child]})
        invalid = [[], {parent: "not a list"}, {parent: [{"kind": "private", "url": child["url"]}]},
                   {parent: [{"kind": "subscription", "url": "https://example.com/sub?token=SECRET"}]},
                   {parent: [{"kind": "subscription", "url": "data/discovered_ocr_configs.txt"}]},
                   {parent: [child, child]}, {"unregistered parent": [child]},
                   {parent: [{"kind": "page", "url": parent}]}, {parent: [child] * 5001}]
        for pending in invalid:
            with self.subTest(pending_type=type(pending).__name__):
                raw = json.dumps(dict(valid, pending_children=pending))
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    DiscoveryStateManager(str(path))
                self.assertEqual(path.read_text(encoding="utf-8"), raw)
        path.write_text(json.dumps(dict(valid, visited_urls=[parent])), encoding="utf-8")
        with self.assertRaises(ValueError):
            DiscoveryStateManager(str(path))
        for owner, field in (("https://GITHUB.com/public/repo", "pending_pages"), (" query ", "pending_queries"), ("gh:", "pending_queries")):
            path.write_text(json.dumps({"version": 1, field: [owner], "pending_children": {owner: [child]}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                DiscoveryStateManager(str(path))


class TestDiscoveryStateValidation(OfflineDiscoveryTestCase):
    def valid_metadata_entry(self, **overrides):
        entry = {"handle": "chan", "last_checked": 1, "last_nodes_yield": 0, "total_nodes_yield": 0,
                 "checks_count": 1, "is_active": True, "sub_url": "https://t.me/s/chan"}
        entry.update(overrides)
        return entry

    def test_bounded_image_results_rejects_non_text(self):
        for bad in ("not a dict", {"k": 123}, {123: "v"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                bounded_image_results(bad)
        self.assertEqual(normalize_channel_handle(123), "")

    def test_serialize_state_second_byte_limit_fires(self):
        from src.discovery import state_manager
        state = {"version": 1, "image_results": {"a" * 64: URI}}
        with patch.object(state_manager, "STATE_MAX_BYTES", 200), \
             patch.object(state_manager, "bounded_image_results", side_effect=lambda results, max_bytes: results), \
             self.assertRaises(ValueError):
            serialize_state(state)

    def test_bounded_checkpoint_read_rejects_oversize_and_bad_encoding(self):
        path = self.root / "checkpoint.txt"
        path.write_bytes(b"x" * 11)
        with self.assertRaises(ValueError):
            read_public_text(path, max_bytes=10)
        path.write_bytes(b"\xff\xfe invalid")
        with self.assertRaises(ValueError):
            read_public_text(path)

    def test_validate_state_rejects_each_bad_section(self):
        cases = [
            {"queries_executed": [123]},
            {"queries_executed": ["bad\nquery"]},
            {"queries_executed": ["https://example.com"]},
            {"pending_pages": ["data/discovered_ocr_configs.txt"]},
            {"external_attempt_order": {"bad key!": 1}},
            {"external_attempt_order": {"good": -1}},
            {"last_updated": -1},
            {"total_api_requests": "many"},
            {"sources_metadata": "not a dict"},
            {"sources_metadata": {"bad key!": {}}},
            {"sources_metadata": {"chan": self.valid_metadata_entry(checks_count=-1)}},
            {"sources_metadata": {"chan": self.valid_metadata_entry(is_active="yes")}},
            {"sources_metadata": {"chan": self.valid_metadata_entry(handle="bad handle!")}},
            {"sources_metadata": {"chan": self.valid_metadata_entry(retained_fingerprints=["not-a-hash"])}},
            {"image_results": {"zz": "x"}},
            {"image_results": {"a" * 64: "not a uri"}},
        ]
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_state(dict({"version": 1}, **bad))

    def test_rejected_channels_drop_from_queue_and_forget_visited_url(self):
        manager = DiscoveryStateManager(str(self.root / "state.json"))
        manager.add_candidates(["chana", "chanb"])
        manager.record_rejected_channels(["chana", "spamchan"])
        self.assertEqual(manager.state["rejected_channels"], ["chana", "spamchan"])
        self.assertNotIn("chana", manager.state["candidate_queue"])
        manager.record_visited_url("https://example.com/readme.md")
        self.assertTrue(manager.is_url_visited("https://example.com/readme.md"))
        manager.forget_visited_url("https://example.com/readme.md")
        self.assertFalse(manager.is_url_visited("https://example.com/readme.md"))

    def test_pending_children_guard_record_visited_url_and_query(self):
        manager = DiscoveryStateManager(str(self.root / "state.json"))
        url = "https://example.com/sub.txt"
        manager.state["pending_children"] = {url: [{"kind": "subscription", "url": url}]}
        with self.assertRaises(ValueError):
            manager.record_visited_url(url)
        manager.state["pending_children"] = {"free v2ray": [{"kind": "page", "url": "https://example.com/x"}]}
        with self.assertRaises(ValueError):
            manager.record_query("free v2ray")

    def test_visited_url_list_truncates_to_recent_5000(self):
        manager = DiscoveryStateManager(str(self.root / "state.json"))
        manager.state["visited_urls"] = [f"https://example.com/{i}" for i in range(5000)]
        self.assertTrue(manager.is_url_visited("https://example.com/0"))
        manager.record_visited_url("https://example.com/new")
        self.assertEqual(len(manager.state["visited_urls"]), 5000)
        self.assertFalse(manager.is_url_visited("https://example.com/0"))
        self.assertTrue(manager.is_url_visited("https://example.com/new"))

    def test_record_visited_empty_handle_noop_pending_guard_and_queue_removal(self):
        manager = DiscoveryStateManager(str(self.root / "state.json"))
        manager.record_visited("", 1, True)
        self.assertEqual(manager.state.get("visited_channels", []), [])
        manager.state["pending_children"] = {"https://t.me/s/chan": [{"kind": "subscription", "url": "https://example.com/sub.txt"}]}
        with self.assertRaises(ValueError):
            manager.record_visited("chan", 1, True)
        manager.state["pending_children"] = {}
        manager.add_candidates(["chan"])
        manager.record_visited("chan", 1, True)
        self.assertNotIn("chan", manager.state["candidate_queue"])
        self.assertIn("chan", manager.state["visited_channels"])

    def test_record_external_source_falls_back_to_source_id_label(self):
        manager = DiscoveryStateManager(str(self.root / "state.json"))
        manager.record_external_source("src-id", "bad name!", 5, "https://example.com/sub.txt")
        self.assertEqual(manager.state["sources_metadata"]["src-id"]["handle"], "src-id")


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
        self.assertEqual(nodes[0].params["path"], "/first")
        self.assertEqual(nodes[0].params["alpn"], ["h2", "http/1.1"])
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
        self.assertEqual(agent.state_manager.state["candidate_queue"], ["proxytwo", "proxythree", "proxyone"])
        self.assertNotIn("proxyone", agent.state_manager.state["visited_channels"])

    def test_exception_after_dequeue_restores_unfinished_suffix(self):
        agent = self.agent(["proxyone", "proxytwo", "proxythree"])
        with self.crawl(agent, [PAGE, RuntimeError("synthetic cancellation")]), self.assertRaises(RuntimeError):
            agent.run_cycle(max_channels=3)
        expected = ["proxythree", "newchan", "proxytwo"]
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

    def test_concurrent_probes_record_each_url_exactly_once(self):
        import concurrent.futures
        import threading
        import time
        agent = self.agent(["proxychan"])
        urls = [f"https://example.com/sub{i}.txt" for i in range(5)]
        seen_threads: set[int] = set()
        lock = threading.Lock()
        def probe(url, **kwargs):
            with lock:
                seen_threads.add(threading.get_ident())
            time.sleep(0.05)
            return 200, URI.encode()
        with patch("src.discovery.agent.fetch_bytes", side_effect=probe):
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                results = list(pool.map(agent.probe_sub_url, urls))
        self.assertEqual(results, [1] * 5)
        visited = agent.state_manager.state["visited_urls"]
        self.assertEqual(len(visited), 5)
        self.assertEqual(len(set(visited)), 5)
        self.assertEqual(len(agent._cycle_probed_urls), 5)
        self.assertGreaterEqual(len(seen_threads), 2)

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
        self.assertEqual(node.params["path"], "/first")
        self.assertEqual(node.params["alpn"], ["h2", "http/1.1"])

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
        def write(path, text):
            if path.name == "state.json":
                raise OSError("synthetic state projection failure")
            atomic_write_text(path, text)
        with self.crawl(agent), patch("src.discovery.agent.atomic_write_text", side_effect=write), self.assertRaises(OSError):
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

    def test_review1_rejected_url_spans_cannot_become_mentions(self):
        agent = self.agent(["proxychan"])
        page = PAGE + ' https://example.com/sub.txt?token=@SecretToken https://user:@OtherToken@example.com/sub.txt https://t.me/s/ApprovedProxy @StandaloneProxy'
        with self.crawl(agent, page):
            agent.run_cycle(max_channels=1)
        queue = agent.state_manager.state["candidate_queue"]
        self.assertIn("approvedproxy", queue)
        self.assertIn("standaloneproxy", queue)
        for path in (self.root / "data/state.json", self.root / "sources.discovered.json", self.root / "DISCOVERY.md"):
            self.assertNotIn("secrettoken", path.read_text(encoding="utf-8").lower())
            self.assertNotIn("othertoken", path.read_text(encoding="utf-8").lower())

    def test_review1_rejected_url_html_delimiters_do_not_expose_tokens(self):
        page = '<a href="https://example.com/sub.txt?token= @SpacedToken">x</a> https://example.com/sub.txt?token=&quot;@EncodedToken&quot; https&#58;//example.com/sub.txt?token=@SchemeToken @StandaloneProxy'
        result = TelegramChannelScraper().parse_channel_page(page)
        self.assertEqual(result.discovered_channels, ["standaloneproxy"])

    def full_old_generation(self):
        self.write_public()
        agent = self.agent()
        old_text = "\n".join(URI.replace("example.com", f"old{i}.example.com") for i in range(400))
        refs = agent._retain(old_text, "https://t.me/s/oldproxy")
        agent.state_manager.record_visited("oldproxy", 400, True, oldest_post_id=90)
        agent.state_manager.state["sources_metadata"]["oldproxy"]["retained_fingerprints"] = sorted(refs)
        agent.state_manager.add_candidates(["newproxy"])
        sources = {"tg-oldproxy": {"id": "tg-oldproxy", "url": "https://t.me/s/oldproxy", "format": "telegram_web"}}
        agent._save_checkpoint(sources, [])
        paths = [self.root / path for path in ("data/discovered_ocr_configs.txt", "sources.discovered.json", "data/state.json", "DISCOVERY.md")]
        return agent, sources, {path: path.read_bytes() for path in paths}

    def test_review2_late_write_failure_preserves_full_acknowledged_generation(self):
        agent, _, old = self.full_old_generation()
        page = '<div data-post="newproxy/1">' + "\n".join(URI.replace("example.com", f"new{i}.example.com") for i in range(400)) + '</div>'
        cache_replaced = False
        failed = False
        def write(path, text):
            nonlocal cache_replaced, failed
            if path.name == "sources.discovered.json" and not failed:
                self.assertTrue(cache_replaced)
                failed = True
                raise OSError("synthetic failure after cache replacement")
            atomic_write_text(path, text)
            if path.name == "discovered_ocr_configs.txt":
                cache_replaced = True
        with self.crawl(agent, page), patch("src.discovery.agent.atomic_write_text", side_effect=write), self.assertRaises(OSError):
            agent.run_cycle(max_channels=1)
        self.assertTrue(failed)
        for path, content in old.items():
            self.assertEqual(path.read_bytes(), content)

    def exit_checkpoint_and_recover(self, after_state=False):
        _, _, old = self.full_old_generation()
        repo = str(Path(__file__).resolve().parents[1])
        script = f'''
import os, sys
sys.path.insert(0, {repo!r})
from unittest.mock import patch
from src.discovery.agent import DiscoveryAgent
from src.storage import atomic_write_text
def forbidden(*args, **kwargs):
    raise AssertionError("unexpected network")
def write(path, text):
    if path.name == "sources.discovered.json" and not {after_state!r}:
        os._exit(73)
    atomic_write_text(path, text)
    if path.name == "state.json" and {after_state!r}:
        os._exit(73)
with patch("src.network._resolve", forbidden), patch("src.discovery.agent.fetch_bytes", forbidden), patch("src.discovery.search_engine.fetch_bytes", forbidden), patch("src.discovery.nemotron_client.fetch_bytes", forbidden), patch("src.discovery.agent.atomic_write_text", write):
    agent = DiscoveryAgent(state_file="data/state.json", discovered_sources_file="sources.discovered.json", initial_seeds=[], nemotron_client=__import__("src.discovery.nemotron_client", fromlist=["NemotronClient"]).NemotronClient(api_key=""))
    agent._load_retained()
    agent._retain("\\n".join({URI!r}.replace("example.com", f"new{{i}}.example.com") for i in range(400)), "https://t.me/s/newproxy")
    agent._save_checkpoint({{"tg-oldproxy": {{"id":"tg-oldproxy", "url":"https://t.me/s/oldproxy"}}}}, [])
'''
        result = subprocess.run([sys.executable, "-B", "-W", "error::ResourceWarning", "-c", script], cwd=self.root, env=dict(os.environ), capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 73, result.stderr.decode())
        self.assertNotEqual((self.root / "data/discovered_ocr_configs.txt").read_bytes(), old[self.root / "data/discovered_ocr_configs.txt"])
        if after_state:
            self.assertNotEqual((self.root / "data/state.json").read_bytes(), old[self.root / "data/state.json"])
        recovered = self.agent()
        self.assertEqual(recovered.state_manager.get_channel_oldest_post_id("oldproxy"), 90)
        for path, content in old.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertFalse((self.root / "data/discovery_checkpoint.json").exists())

    def test_review2_process_exit_after_cache_replacement_recovers_old_generation(self):
        self.exit_checkpoint_and_recover()

    def test_review2_process_exit_after_state_replacement_before_commit_recovers_old_generation(self):
        self.exit_checkpoint_and_recover(after_state=True)

    def test_review3_failed_expansion_child_stays_retryable_when_channel_repeats_it(self):
        agent = self.agent(["proxychan"])
        child = "https://example.com/sub.txt"
        def expand(discovered, existing, **kwargs):
            agent._consider_url(child, discovered, existing)
        with patch.object(agent, "_expand_queries_via_nemotron", side_effect=expand), patch.object(agent.tg_scraper, "fetch_channel_page", return_value='<div data-post="proxychan/1">' + child + '</div>'), patch("src.discovery.agent.fetch_bytes", side_effect=NetworkError("synthetic")) as transport:
            agent.run_cycle(max_channels=1)
        self.assertEqual(transport.call_count, 1)
        self.assertNotIn("proxychan", agent.state_manager.state["visited_channels"])
        self.assertIn("proxychan", agent.state_manager.state["candidate_queue"])

    def test_review4_no_key_query_retries_failed_child_across_cycles(self):
        agent = self.agent()
        query = 'site:t.me "vless://" reality'
        child = "https://example.com/sub.txt"
        def search(url, **kwargs):
            return (200, ('<a href="' + child + '">public</a>').encode()) if "duckduckgo" in url else (200, b'{"items":[]}')
        with patch("src.discovery.search_engine.fetch_bytes", side_effect=search), patch("src.discovery.agent.fetch_bytes", side_effect=NetworkError("synthetic")) as probe:
            agent.run_cycle(max_channels=1)
            self.assertNotIn(query, agent.state_manager.state["queries_executed"])
            self.agent().run_cycle(max_channels=1)
            self.assertEqual(probe.call_count, 2)

    def test_review4_no_key_page_retries_child_and_unfinished_suffix(self):
        parent = "https://github.com/public/repo"
        children = [f"https://example.com/sub{i}.txt" for i in range(7)]
        agent = self.agent()
        with patch("src.discovery.search_engine.fetch_bytes", return_value=(200, " ".join(children).encode())), patch("src.discovery.agent.fetch_bytes", side_effect=NetworkError("synthetic")):
            agent._analyze_webpage_via_nemotron(parent, {}, set())
            self.assertFalse(agent.state_manager.is_url_visited(parent))
        agent._save_checkpoint({}, [])
        for _ in range(3):
            agent = self.agent()
            discovered = {row["id"]: row for row in agent._load_registry(self.root / "sources.discovered.json")}
            with patch("src.discovery.search_engine.fetch_bytes", return_value=(200, " ".join(children).encode())), patch("src.discovery.agent.fetch_bytes", return_value=(200, URI.encode())):
                agent._analyze_webpage_via_nemotron(parent, discovered, set())
            agent._save_checkpoint(discovered, [])
        self.assertTrue(agent.state_manager.is_url_visited(parent))
        self.assertTrue(all(agent.state_manager.is_url_visited(child) for child in children))

    def test_review5_five_failed_feeds_do_not_starve_sixth_or_clear_last_good(self):
        rows = [{"id": f"sub-{i}", "url": f"https://example.com/sub{i}.txt"} for i in range(6)]
        (self.root / "sources.discovered.json").write_text(json.dumps({"sources": rows}))
        agent = self.agent()
        for row in rows:
            agent.state_manager.record_external_source(row["id"], row["id"], 10, row["url"])
        good = dict(agent.state_manager.state["sources_metadata"]["sub-0"])
        def fetch(url, **kwargs):
            if url.endswith("sub5.txt"):
                return 410, b""
            raise NetworkError("synthetic")
        with self.crawl(agent), patch("src.discovery.agent.fetch_bytes", side_effect=fetch):
            agent.run_cycle(max_channels=1)
        reloaded = self.agent()
        with self.crawl(reloaded), patch("src.discovery.agent.fetch_bytes", side_effect=fetch):
            sources = reloaded.run_cycle(max_channels=1)
        self.assertNotIn("sub-5", {s["id"] for s in sources})
        self.assertEqual(reloaded.state_manager.state["sources_metadata"]["sub-0"], good)

    def test_review6_total_image_state_bytes_are_bounded_before_checkpoint(self):
        agent = self.agent()
        long_uri = parse_proxy_uri(URI.split("?")[0] + "?path=" + "x" * 10000).to_uri(include_formatted_remark=False)
        agent.nemotron.image_results = {f"{i:064x}": "\n".join([long_uri] * 3) for i in range(400)}
        agent._save_checkpoint({}, [])
        state = json.loads((self.root / "data/state.json").read_text())
        self.assertLessEqual(len((self.root / "data/state.json").read_bytes()), 8 * 1024 * 1024)
        self.assertLessEqual(len(json.dumps(state["image_results"]).encode()), 2 * 1024 * 1024)
        self.assertTrue(all(parse_proxy_uri(uri) for text in state["image_results"].values() for uri in text.splitlines()))

    def test_review7_curated_retained_id_collision_does_not_hide_feed_from_load_sources(self):
        self.write_public([{"id": "nemotron-vision-ocr", "url": "https://example.com/unrelated.txt"}])
        agent = self.agent(["proxychan"], key="synthetic")
        page = '<div data-post="proxychan/1"><img src="https://cdn.telegram.org/public.png"></div>'
        with self.crawl(agent, page), patch("src.discovery.nemotron_client.fetch_bytes", side_effect=[(200, IMAGE), completion(URI)]):
            agent.run_cycle(max_channels=1, max_api_requests=3)
        sources = load_sources("sources.json", "sources.discovered.json")
        cache = next((s for s in sources if s.url == "data/discovered_ocr_configs.txt"), None)
        self.assertIsNotNone(cache)
        nodes, _, stats = fetch_source(cache)
        self.assertTrue(stats.success)
        self.assertEqual(len(nodes), 1)

    def test_review7_cross_registry_id_conflict_rejected_before_writes(self):
        self.write_public([{"id": "same", "url": "https://example.com/curated.txt"}])
        registry = self.root / "sources.discovered.json"
        raw = '{"sources":[{"id":"same","url":"https://example.com/discovered.txt"}]}'
        registry.write_text(raw)
        with self.assertRaises(ValueError):
            self.agent().run_cycle(max_channels=0)
        self.assertEqual(registry.read_text(), raw)

    def test_review4_retained_page_parent_keeps_retry_work_when_search_result_disappears(self):
        parent, child = "https://github.com/public/repo", "https://example.com/sub.txt"
        agent = self.agent()
        phase = 0
        child_attempts = 0
        def search(url, **kwargs):
            if "duckduckgo" in url:
                return 200, ('<a href="' + parent + '">public</a>').encode() if phase == 0 else b""
            if url == parent:
                return 200, (URI + " " + child).encode()
            return 200, b'{"items":[]}'
        def probe(url, **kwargs):
            nonlocal child_attempts
            if url == child:
                child_attempts += 1
                if phase == 0:
                    raise NetworkError("synthetic")
            return 200, URI.encode()
        with patch("src.discovery.search_engine.fetch_bytes", side_effect=search), patch("src.discovery.agent.fetch_bytes", side_effect=probe):
            agent.run_cycle(max_channels=1)
            self.assertFalse(agent.state_manager.is_url_visited(parent))
            phase = 1
            agent = self.agent()
            agent.run_cycle(max_channels=1)
        self.assertEqual(child_attempts, 2)
        self.assertTrue(agent.state_manager.is_url_visited(parent))

    def test_review2_checkpoint_target_redirection_rejected(self):
        resolve = Path.resolve
        def redirected(path, *args, **kwargs):
            if path.name == "sources.discovered.json":
                return self.root.resolve() / "other-public.json"
            return resolve(path, *args, **kwargs)
        with patch("pathlib.Path.resolve", redirected), self.assertRaises(ValueError):
            self.agent()

    def test_review2_malformed_journal_never_changes_projections(self):
        _, _, old = self.full_old_generation()
        journal = self.root / "data/discovery_checkpoint.json"
        targets = {"cache": "data/discovered_ocr_configs.txt", "registry": "sources.discovered.json", "state": "data/state.json", "leaderboard": "DISCOVERY.md"}
        previous = dict(zip(targets, (content.decode("utf-8") for content in old.values())))
        for data in ({}, {"version": 1, "targets": targets, "previous": dict(previous, cache="invalid retained config")}):
            raw = json.dumps(data, ensure_ascii=False)
            journal.write_text(raw, encoding="utf-8")
            with patch("src.discovery.agent.atomic_write_text") as write, self.assertRaises(ValueError):
                self.agent()
            write.assert_not_called()
            self.assertEqual(journal.read_text(encoding="utf-8"), raw)
            for path, content in old.items():
                self.assertEqual(path.read_bytes(), content)
        journal.unlink()

    def test_review2_interrupted_recovery_keeps_journal_until_all_old_projections_restored(self):
        _, _, old = self.full_old_generation()
        targets = {"cache": "data/discovered_ocr_configs.txt", "registry": "sources.discovered.json", "state": "data/state.json", "leaderboard": "DISCOVERY.md"}
        previous = dict(zip(targets, (content.decode("utf-8") for content in old.values())))
        journal = self.root / "data/discovery_checkpoint.json"
        journal.write_text(json.dumps({"version": 1, "targets": targets, "previous": previous}, ensure_ascii=False), encoding="utf-8")
        (self.root / "data/state.json").write_text('{"version":1}', encoding="utf-8")
        def write(path, text):
            if path.name == "state.json":
                raise OSError("synthetic interrupted recovery")
            atomic_write_text(path, text)
        with patch("src.discovery.agent.atomic_write_text", side_effect=write), self.assertRaises(OSError):
            self.agent()
        self.assertTrue(journal.exists())
        self.agent()
        self.assertFalse(journal.exists())
        for path, content in old.items():
            self.assertEqual(path.read_bytes(), content)

    def test_review6_invalid_new_generation_is_rejected_before_any_write(self):
        for invalid in ("cache", "registry", "state-budget"):
            with self.subTest(invalid=invalid):
                agent, sources, old = self.full_old_generation()
                if invalid == "cache":
                    agent._retained = {"0" * 64: "not a connection"}
                elif invalid == "registry":
                    sources["bad"] = {"id": "bad", "url": "https://user:SECRET@example.com/sub.txt"}
                else:
                    agent.state_manager.state["visited_urls"] = ["https://example.com/" + "x" * 10000 + f"/{i}" for i in range(1000)]
                with patch("src.discovery.agent.atomic_write_text") as write, self.assertRaises(ValueError):
                    agent._save_checkpoint(sources, [])
                write.assert_not_called()
                for path, content in old.items():
                    self.assertEqual(path.read_bytes(), content)
                self.assertFalse((self.root / "data/discovery_checkpoint.json").exists())

    def test_review7_generated_channel_and_subscription_ids_do_not_overwrite_curated_rows(self):
        child = "https://example.com/sub.txt"
        import hashlib
        preferred = "sub-" + hashlib.sha256(child.encode()).hexdigest()[:10]
        self.write_public([{"id": "tg-proxychan", "url": "https://example.com/other.txt"}, {"id": preferred, "url": "https://example.com/another.txt"}])
        agent = self.agent(["proxychan"])
        with self.crawl(agent, PAGE + " " + child), patch("src.discovery.agent.fetch_bytes", return_value=(200, URI.encode())):
            agent.run_cycle(max_channels=1)
        sources = load_sources("sources.json", "sources.discovered.json")
        self.assertEqual(len({source.id for source in sources}), len(sources))
        self.assertIn(child, {source.url for source in sources})
        self.assertIn("https://t.me/s/proxychan", {source.url for source in sources})

    def test_fix2_A_combined_encoded_url_never_persists_token(self):
        agent = self.agent(["proxychan"])
        exact = '<div data-post="proxychan/1">https&#58;//example.com/sub.txt?token=&quot;@DoubleToken&quot;</div>'
        page = exact + ' <a href="https&#58;//t.me/s/ApprovedProxy">public</a> @StandaloneProxy'
        with self.crawl(agent, page):
            agent.run_cycle(max_channels=1)
        self.assertIn("approvedproxy", agent.state_manager.state["candidate_queue"])
        self.assertIn("standaloneproxy", agent.state_manager.state["candidate_queue"])
        for name in ("data/state.json", "sources.discovered.json", "DISCOVERY.md"):
            self.assertNotIn("doubletoken", (self.root / name).read_text(encoding="utf-8").lower())

    def test_fix2_B_changed_query_cannot_discard_saved_direct_child(self):
        self.write_public()
        child = "https://example.com/sub.txt"
        query = 'site:t.me "vless://" reality'
        phase, attempts = 0, 0
        def search(url, **kwargs):
            if "duckduckgo" in url:
                return 200, ('<a href="' + child + '">public</a>').encode() if phase == 0 else b""
            return 200, b'{"items":[]}'
        def probe(url, **kwargs):
            nonlocal attempts
            self.assertEqual(url, child)
            attempts += 1
            if phase < 2:
                raise NetworkError("synthetic retryable child")
            return 200, URI.encode()
        with patch("src.discovery.search_engine.fetch_bytes", side_effect=search), patch("src.discovery.agent.fetch_bytes", side_effect=probe):
            self.agent().run_cycle(max_channels=1)
            phase = 1
            agent = self.agent()
            agent.run_cycle(max_channels=1)
            self.assertEqual(attempts, 2)
            self.assertNotIn(query, agent.state_manager.state["queries_executed"])
            self.assertIn(child, json.dumps(agent.state_manager.state.get("pending_children", {})))
            phase = 2
            agent = self.agent()
            sources = agent.run_cycle(max_channels=1)
        self.assertEqual(attempts, 3)
        self.assertIn(query, agent.state_manager.state["queries_executed"])
        self.assertTrue(agent.state_manager.is_url_visited(child))
        self.assertIn(child, {source["url"] for source in sources})

    def test_fix2_B_changed_page_cannot_discard_saved_children_or_suffix(self):
        self.write_public()
        parent = "https://github.com/public/repo"
        children = [f"https://example.com/sub{i}.txt" for i in range(7)]
        agent = self.agent()
        with patch("src.discovery.search_engine.fetch_bytes", return_value=(200, " ".join(children).encode())), patch("src.discovery.agent.fetch_bytes", side_effect=NetworkError("synthetic retryable child")):
            agent._analyze_webpage_via_nemotron(parent, {}, set())
        agent._save_checkpoint({}, [])
        phase, attempts = 1, []
        def search(url, **kwargs):
            return (200, b'{"items":[]}') if "api.github.com" in url else (200, b"")
        def probe(url, **kwargs):
            self.assertIn(url, children)
            attempts.append(url)
            if phase == 1:
                raise NetworkError("synthetic retryable child")
            return 200, URI.encode()
        with patch("src.discovery.search_engine.fetch_bytes", side_effect=search), patch("src.discovery.agent.fetch_bytes", side_effect=probe):
            agent = self.agent()
            agent.run_cycle(max_channels=1)
            self.assertTrue(attempts)
            self.assertFalse(agent.state_manager.is_url_visited(parent))
            saved = json.dumps(agent.state_manager.state.get("pending_children", {}))
            self.assertTrue(all(child in saved for child in children))
            phase = 2
            for _ in range(3):
                agent = self.agent()
                agent.run_cycle(max_channels=1)
        self.assertTrue(agent.state_manager.is_url_visited(parent))
        self.assertTrue(all(agent.state_manager.is_url_visited(child) for child in children))

    @patch("src.discovery.agent.time.monotonic", return_value=0)
    def test_fix2_B_search_deadline_saves_unattempted_suffix_for_missing_query(self, clock):
        self.write_public()
        children = [f"https://example.com/sub{i}.txt" for i in range(8)]
        def search(url, **kwargs):
            clock.return_value = 10
            return 200, " ".join(f'<a href="{child}">public</a>' for child in children).encode()
        with patch("src.discovery.search_engine.fetch_bytes", side_effect=search), patch("src.discovery.agent.fetch_bytes") as probe:
            self.agent().run_cycle(max_channels=1, max_time_seconds=10)
            probe.assert_not_called()
        saved = json.loads((self.root / "data/state.json").read_text(encoding="utf-8"))
        self.assertTrue(all(child in json.dumps(saved.get("pending_children", {})) for child in children))
        clock.return_value = 0
        with patch("src.discovery.search_engine.fetch_bytes", return_value=(404, b"")), patch("src.discovery.agent.fetch_bytes", return_value=(200, URI.encode())):
            for _ in range(2):
                agent = self.agent()
                agent.run_cycle(max_channels=1)
        self.assertTrue(all(agent.state_manager.is_url_visited(child) for child in children))

    def test_fix2_B_disappearing_page_replays_child_before_acknowledging_parent(self):
        parent, child = "https://github.com/public/repo", "https://example.com/sub.txt"
        agent = self.agent()
        with patch("src.discovery.search_engine.fetch_bytes", return_value=(200, child.encode())), patch("src.discovery.agent.fetch_bytes", side_effect=NetworkError("synthetic")):
            agent._analyze_webpage_via_nemotron(parent, {}, set())
        agent._save_checkpoint({}, [])
        agent = self.agent()
        with patch("src.discovery.search_engine.fetch_bytes", return_value=(404, b"")), patch("src.discovery.agent.fetch_bytes", return_value=(200, URI.encode())) as probe:
            agent.run_cycle(max_channels=1)
        self.assertEqual([call.args[0] for call in probe.call_args_list], [child])
        self.assertTrue(agent.state_manager.is_url_visited(child))
        self.assertTrue(agent.state_manager.is_url_visited(parent))
        self.assertFalse(agent.state_manager.state.get("pending_children", {}))

    def test_fix2_B_pending_children_do_not_skip_unfinished_page_extraction(self):
        parent, child = "https://github.com/public/repo", "https://example.com/sub.txt"
        agent = self.agent(key="synthetic")
        with patch("src.discovery.search_engine.fetch_bytes", return_value=(200, child.encode())), patch.object(agent.nemotron, "complete_json", side_effect=RuntimeError("synthetic interrupted extraction")):
            with self.assertRaises(RuntimeError):
                agent._analyze_webpage_via_nemotron(parent, {}, set())
        agent._save_checkpoint({}, [])
        agent = self.agent()
        with patch("src.discovery.search_engine.fetch_bytes", return_value=(200, URI.encode())), patch("src.discovery.agent.fetch_bytes", return_value=(200, URI.encode())):
            agent._analyze_webpage_via_nemotron(parent, {}, set())
        self.assertTrue(agent.state_manager.is_url_visited(child))
        self.assertTrue(agent.state_manager.is_url_visited(parent))
        self.assertTrue(agent._retained)

    def test_fix2_B_changed_github_results_replay_saved_suffix_with_probe_cap(self):
        phase, attempts = 0, []
        repos = [{"full_name": f"public/repo{i}", "default_branch": "master"} for i in range(7)]
        def search(url, **kwargs):
            if "api.github.com" in url:
                return 200, json.dumps({"items": repos if phase == 0 else []}).encode()
            return 200, b""
        def probe(url, **kwargs):
            attempts.append(url)
            return 200, URI.encode()
        with patch("src.discovery.search_engine.fetch_bytes", side_effect=search), patch("src.discovery.agent.fetch_bytes", side_effect=probe):
            agent = self.agent()
            agent.run_cycle(max_channels=1)
            self.assertEqual(len(attempts), 18)
            self.assertNotIn("gh:v2ray-configs sub", agent.state_manager.state["queries_executed"])
            phase = 1
            agent = self.agent()
            agent.run_cycle(max_channels=1)
        # The second cycle also refreshes five already checked public feeds.
        self.assertEqual(len(attempts), 33)
        self.assertEqual(len(set(attempts)), 28)
        self.assertIn("gh:v2ray-configs sub", agent.state_manager.state["queries_executed"])
        self.assertFalse(agent.state_manager.state.get("pending_children", {}))

    def test_fix2_B_child_count_and_byte_limits_preserve_old_generation_without_writes(self):
        for count, padding in ((5001, ""), (3000, "x" * 3000)):
            with self.subTest(count=count):
                agent, sources, old = self.full_old_generation()
                parents = ["https://github.com/public/repo1", "https://github.com/public/repo2"]
                agent.state_manager.state["pending_pages"] = parents
                children = [{"kind": "subscription", "url": f"https://example.com/{padding}/sub{i}.txt"} for i in range(count)]
                agent.state_manager.state["pending_children"] = {parents[0]: children[:2500], parents[1]: children[2500:]}
                with patch("src.discovery.agent.atomic_write_text") as write, self.assertRaises(ValueError):
                    agent._save_checkpoint(sources, [])
                write.assert_not_called()
                for path, raw in old.items():
                    self.assertEqual(path.read_bytes(), raw)
                self.assertFalse((self.root / "data/discovery_checkpoint.json").exists())

    def test_fix2_C_non_public_or_wrong_role_targets_reject_before_reads(self):
        dotenv = self.root / ".env"
        sentinel = b"SYNTHETIC_CREDENTIAL=fixture-only\r\n"
        dotenv.write_bytes(sentinel)
        cases = [
            {"leaderboard_file": ".env"},
            {"leaderboard_file": "local_subs/DISCOVERY.md"},
            {"leaderboard_file": ".superpowers/DISCOVERY.md"},
            {"leaderboard_file": "data/discovery_state.json"},
            {"leaderboard_file": "guiNConfig.json"},
            {"state_file": "data/credentials.json"},
            {"state_file": ".env"},
            {"state_file": "src/discovery/state_manager.py"},
            # sources.json is NOT in this list anymore: it is the unified
            # registry now, so pointing the registry at it is legitimate.
            {"discovered_sources_file": "local_subs/sources.discovered.json"},
            {"leaderboard_file": "test_DISCOVERY.md"},
            {"state_file": "data/test_agent_state.json"},
            {"discovered_sources_file": "test_sources.discovered.json"},
        ]
        for options in cases:
            with self.subTest(options=options), patch("src.discovery.agent.read_public_text", wraps=read_public_text) as read, patch("pathlib.Path.open", autospec=True, side_effect=Path.open) as opened, patch("src.discovery.agent.atomic_write_text") as write:
                with self.assertRaises(ValueError):
                    DiscoveryAgent(initial_seeds=[], nemotron_client=NemotronClient(api_key=""), **options)
                read.assert_not_called()
                opened.assert_not_called()
                write.assert_not_called()
        self.assertEqual(dotenv.read_bytes(), sentinel)
        self.assertFalse((self.root / "data/discovery_checkpoint.json").exists())

    def test_fix2_C_reconfigured_private_target_rejects_before_snapshot_or_recovery_read(self):
        agent = self.agent()
        dotenv = self.root / ".env"
        dotenv.write_bytes(b"SYNTHETIC_CREDENTIAL=fixture-only\r\n")
        agent._checkpoint_paths["leaderboard"] = dotenv
        for operation in (lambda: agent._save_checkpoint({}, []), agent._recover_checkpoint):
            with patch("pathlib.Path.open", autospec=True, side_effect=Path.open) as opened, patch("src.discovery.agent.atomic_write_text") as write:
                with self.assertRaises(ValueError):
                    operation()
                opened.assert_not_called()
                write.assert_not_called()
        self.assertFalse((self.root / "data/discovery_checkpoint.json").exists())

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive filesystem regression")
    def test_fix2_D_absent_case_alias_roles_reject_before_serialization_or_writes(self):
        self.assertFalse((self.root / "data/discovered_ocr_configs.txt").exists())
        for alias in ("data/DISCOVERED_OCR_CONFIGS.TXT", "DATA/DISCOVERED_OCR_CONFIGS.TXT"):
            with self.subTest(alias=alias), patch("src.discovery.agent.read_public_text", wraps=read_public_text) as read, patch("src.discovery.agent.atomic_write_text") as write:
                with self.assertRaisesRegex(ValueError, "Conflicting"):
                    DiscoveryAgent(state_file=alias, initial_seeds=[], nemotron_client=NemotronClient(api_key=""))
                read.assert_not_called()
                write.assert_not_called()
        self.assertFalse((self.root / "data/discovered_ocr_configs.txt").exists())

    @patch("src.discovery.agent.time.monotonic", return_value=0)
    def test_fix3_E_cross_linked_parsed_pages_progress_across_three_reloads(self, clock):
        parents = ["https://example.com/a.html", "https://example.com/b.html"]
        query = 'site:t.me "vless://" reality'
        phase, parent_reads, definitive_probes = 0, [], []
        def search(url, **kwargs):
            self.assertEqual(kwargs["deadline"], 10)
            if url in parents:
                if phase:
                    parent_reads.append(url)
                    return 404, b""
                if url == parents[1]:
                    clock.return_value = 10
                return 200, f'<a href="{parents[1] if url == parents[0] else parents[0]}">public</a>'.encode()
            if "duckduckgo" in url:
                return 200, (" ".join(f'<a href="{parent}">public</a>' for parent in parents).encode() if phase == 0 else b"")
            return 200, b'{"items":[]}'
        def probe(url, **kwargs):
            self.assertIn(url, parents)
            self.assertEqual(kwargs["deadline"], 10)
            if phase == 0:
                raise NetworkError("synthetic transient linked-page probe")
            definitive_probes.append(url)
            return 404, b""
        with patch("src.discovery.search_engine.fetch_bytes", side_effect=search), patch("src.discovery.agent.fetch_bytes", side_effect=probe):
            agent = self.agent()
            agent.run_cycle(max_channels=1, max_time_seconds=10)
            self.assertEqual(set(agent.state_manager.state["pending_pages"]), set(parents))
            saved = agent.state_manager.state["pending_children"]
            self.assertEqual(saved[parents[0]], [{"kind": "subscription", "url": parents[1]}])
            self.assertEqual(saved[parents[1]], [{"kind": "subscription", "url": parents[0]}])
            self.assertIn(query, agent.state_manager.state["pending_queries"])
            phase = 1
            for _ in range(3):
                clock.return_value = 0
                agent = self.agent()
                agent.run_cycle(max_channels=1, max_time_seconds=10)
        self.assertGreater(len(parent_reads) + len(definitive_probes), 0)
        self.assertEqual(parent_reads, parents)
        self.assertTrue(all(agent.state_manager.is_url_visited(parent) for parent in parents))
        self.assertIn(query, agent.state_manager.state["queries_executed"])
        self.assertFalse(agent.state_manager.state.get("pending_children", {}))

    def interrupted_github_readme_phase(self, interruption):
        query = "fix3 original query " + interruption
        repo = "public/direct-" + interruption
        readme_repo = "public/readme-sentinel-" + interruption
        child_urls = [f"https://raw.githubusercontent.com/{repo}/main/{name}" for name in ("sub.txt", "configs.txt", "All_Configs_Sub.txt", "sub")]
        readme_url = f"https://raw.githubusercontent.com/{readme_repo}/main/README.md"
        phase, original_query_reads, probes = 0, [0, 0], []
        with patch("src.discovery.agent.time.monotonic", return_value=0) as clock:
            def search(url, **kwargs):
                self.assertEqual(kwargs["deadline"], 10)
                if "api.github.com" in url:
                    searched = parse_qs(urlsplit(url).query)["q"][0]
                    if searched != query:
                        return 200, b'{"items":[]}'
                    original_query_reads[phase] += 1
                    if phase == 0 and original_query_reads[phase] == 1:
                        if interruption == "deadline":
                            clock.return_value = 10
                        return 200, json.dumps({"items": [{"full_name": repo, "default_branch": "main"}]}).encode()
                    if phase == 0:
                        raise RuntimeError("synthetic interrupted original README search")
                    if original_query_reads[phase] == 1:
                        return 200, b'{"items":[]}'  # Direct results changed; old children still need replay.
                    return 200, json.dumps({"items": [{"full_name": readme_repo, "default_branch": "main"}]}).encode()
                return 200, URI.encode() if url == readme_url else b""
            def probe(url, **kwargs):
                self.assertIn(url, child_urls + [readme_url])
                self.assertEqual(kwargs["deadline"], 10)
                if url in child_urls:
                    probes.append(url)
                return 200, URI.encode()
            suggestions = {"web_queries": ["fix3 web fixture"], "github_queries": [query]}
            with patch("src.discovery.search_engine.fetch_bytes", side_effect=search), patch("src.discovery.agent.fetch_bytes", side_effect=probe), patch("src.discovery.nemotron_client.NemotronClient.complete_json", return_value=suggestions):
                agent = self.agent(key="synthetic")
                if interruption == "exception":
                    with self.assertRaisesRegex(RuntimeError, "original README"):
                        agent.run_cycle(max_channels=1, max_time_seconds=10, max_api_requests=10)
                else:
                    agent.run_cycle(max_channels=1, max_time_seconds=10, max_api_requests=10)
                self.assertNotIn("gh:" + query, agent.state_manager.state["queries_executed"])
                self.assertTrue(all(child in json.dumps(agent.state_manager.state["pending_children"]) for child in child_urls))
                self.assertFalse(probes)
                phase = 1
                clock.return_value = 0
                agent = self.agent(key="synthetic")
                sources = agent.run_cycle(max_channels=1, max_time_seconds=10, max_api_requests=10)
            self.assertEqual(original_query_reads, [1 if interruption == "deadline" else 2, 2])
            self.assertEqual(set(probes), set(child_urls))
            self.assertEqual(len(probes), 4)
            self.assertTrue(agent.state_manager.is_url_visited(readme_url))
            self.assertIn(readme_url, {source["url"] for source in sources})
            self.assertIn("gh:" + query, agent.state_manager.state["queries_executed"])
            self.assertTrue(agent._retained)

    def test_fix3_F_deadline_before_original_readme_phase(self):
        self.interrupted_github_readme_phase("deadline")

    def test_fix3_F_exception_during_original_readme_phase(self):
        self.interrupted_github_readme_phase("exception")

    def test_fix3_F_replayed_probes_reduce_fresh_query_probe_cap(self):
        query, old_child = "fix3 probe cap query", "https://example.com/old-sub.txt"
        agent = self.agent()
        agent.state_manager.state["pending_queries"] = ["gh:" + query]
        agent._queue_children("gh:" + query, [old_child], "subscription", {}, set())
        agent._save_checkpoint({}, [])
        repos = [{"full_name": f"public/new-repo{i}", "default_branch": "main"} for i in range(7)]
        def search(url, **kwargs):
            if "api.github.com" in url and parse_qs(urlsplit(url).query)["q"][0] == query:
                return 200, json.dumps({"items": repos}).encode()
            return 200, b'{"items":[]}' if "api.github.com" in url else b""
        probes = []
        def probe(url, **kwargs):
            probes.append(url)
            return 200, URI.encode()
        with patch("src.discovery.search_engine.fetch_bytes", side_effect=search), patch("src.discovery.agent.fetch_bytes", side_effect=probe):
            agent = self.agent()
            agent.run_cycle(max_channels=1)
        self.assertEqual(probes[0], old_child)
        self.assertEqual(len(probes), 18)
        self.assertTrue(agent.state_manager.is_url_visited(old_child))
        self.assertNotIn("gh:" + query, agent.state_manager.state["queries_executed"])
        self.assertEqual(len(agent.state_manager.state["pending_children"]["gh:" + query]), 11)


if __name__ == "__main__":
    unittest.main()
