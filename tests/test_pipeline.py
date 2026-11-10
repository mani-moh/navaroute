from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.categorizer import publish_subscriptions
from src.dedup import deduplicate_nodes
from src.fetcher import fetch_all_sources, fetch_source, load_sources
from src.healthcheck import (
    build_xray_outbound,
    check_nodes_health,
    probe_node_socket,
    probe_node_xray,
)
from src.main import build_arg_parser, main
from src.models import ProxyNode, SourceConfig, SourceStats

BASE_DIR = Path(__file__).resolve().parent.parent


class TestHealthcheck(unittest.TestCase):
    def test_probe_node_unreachable_socket(self):
        # Port 1 on non-routable IP
        node = ProxyNode(
            scheme="vless",
            address="192.0.2.1",
            port=1,
            credential="uuid",
        )
        passed, latency = probe_node_socket(node, timeout=0.3)
        self.assertFalse(passed)
        self.assertIsNone(latency)

    def test_probe_node_reachable_tcp_socket(self):
        # Bind a real temporary local TCP server
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.bind(("127.0.0.1", 0))
        server_sock.listen(1)
        port = server_sock.getsockname()[1]

        def _accept_conn():
            try:
                conn, _ = server_sock.accept()
                time.sleep(0.01)
                conn.close()
            except Exception:
                pass

        th = threading.Thread(target=_accept_conn, daemon=True)
        th.start()

        node = ProxyNode(
            scheme="ss",
            address="127.0.0.1",
            port=port,
            credential="pass",
        )
        passed, latency = probe_node_socket(node, timeout=1.5)
        server_sock.close()

        self.assertTrue(passed)
        self.assertIsNotNone(latency)
        self.assertGreater(latency, 0.0)

    def test_check_nodes_health_prioritizes_verified(self):
        n1 = ProxyNode(
            scheme="vless",
            address="192.0.2.1",
            port=1,
            credential="u1",
            is_verified_source=True,
        )
        n2 = ProxyNode(
            scheme="vless",
            address="192.0.2.2",
            port=1,
            credential="u2",
            is_verified_source=False,
        )
        # Should execute without errors and mutate nodes
        check_nodes_health([n1, n2], max_nodes=10, timeout=0.2)
        self.assertFalse(n1.passed_healthcheck)
        self.assertFalse(n2.passed_healthcheck)

    def test_build_xray_outbound_and_fallback(self):
        vless_node = ProxyNode(
            scheme="vless",
            address="1.2.3.4",
            port=443,
            credential="uuid-vless",
            params={"type": "ws", "security": "tls", "sni": "test.com", "path": "/ws"},
        )
        ob_vless = build_xray_outbound(vless_node)
        self.assertIsNotNone(ob_vless)
        self.assertEqual(ob_vless["protocol"], "vless")
        self.assertEqual(ob_vless["streamSettings"]["network"], "ws")
        self.assertEqual(ob_vless["streamSettings"]["security"], "tls")

        trojan_node = ProxyNode(
            scheme="trojan",
            address="5.6.7.8",
            port=443,
            credential="secret-trojan",
            params={"type": "grpc", "serviceName": "tr-grpc", "security": "reality", "pbk": "test-pbk"},
        )
        ob_trojan = build_xray_outbound(trojan_node)
        self.assertIsNotNone(ob_trojan)
        self.assertEqual(ob_trojan["protocol"], "trojan")
        self.assertEqual(ob_trojan["streamSettings"]["security"], "reality")

        # Invalid node
        invalid_node = ProxyNode(scheme="unknown", address="1.1.1.1", port=0, credential="c")
        self.assertIsNone(build_xray_outbound(invalid_node))

        # probe_node_xray with non-existent binary gracefully falls back to socket probe
        passed, latency = probe_node_xray(vless_node, xray_bin="non_existent_xray_bin", timeout=0.2)
        self.assertFalse(passed)
        self.assertIsNone(latency)

    @patch("src.healthcheck.probe_batch_xray")
    def test_batched_xray_config_and_multiround_intersection(self, mock_probe_batch):
        from src.healthcheck import _build_xray_batch_config

        n1 = ProxyNode(scheme="vless", address="1.1.1.1", port=443, credential="u1")
        n2 = ProxyNode(scheme="vmess", address="2.2.2.2", port=443, credential="u2")
        n3 = ProxyNode(scheme="trojan", address="3.3.3.3", port=443, credential="u3")

        cfg = _build_xray_batch_config([(0, build_xray_outbound(n1))], [25000])
        self.assertEqual(cfg["outbounds"][0]["protocol"], "blackhole")
        self.assertEqual(cfg["inbounds"][0]["protocol"], "http")
        self.assertEqual(cfg["inbounds"][0]["port"], 25000)
        self.assertEqual(cfg["routing"]["rules"][0]["inboundTag"], ["in_0"])

        # Round 1 passes n1 and n2; Round 2 ([n1, n2]) only passes index 1 (n2) -> strictly n2 survives
        mock_probe_batch.side_effect = [
            {0: 120.0, 1: 80.0},
            {1: 90.0},
        ]
        check_nodes_health(
            [n1, n2, n3],
            max_nodes=10,
            xray_bin="fake_xray",
            use_xray=True,
            rounds=2,
            batch_size=10,
        )
        self.assertFalse(n1.passed_healthcheck)
        self.assertTrue(n2.passed_healthcheck)
        self.assertEqual(n2.latency_ms, 85.0)
        self.assertFalse(n3.passed_healthcheck)


class TestCategorizer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temp_dir.name) / "subs"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_publish_subscriptions(self):
        public_verified = ProxyNode(
            scheme="vless",
            address="1.1.1.1",
            port=443,
            credential="pub-uuid-1",
            params={"security": "reality", "pbk": "test-pbk", "sni": "de.host.net"},
            remark="Germany Reality",
            country_code="DE",
            is_verified_source=True,
            passed_healthcheck=True,
            latency_ms=30.0,
        )

        public_iran = ProxyNode(
            scheme="trojan",
            address="2.2.2.2",
            port=443,
            credential="pub-pass-2",
            params={"type": "ws", "security": "tls", "sni": "ir.host.net"},
            tags={"iran"},
            remark="Iran WS",
            country_code="IR",
            is_verified_source=False,
            passed_healthcheck=True,
            latency_ms=60.0,
        )

        special_node = ProxyNode(
            scheme="vless",
            address="special.example.com",
            port=443,
            credential="pub-uuid-3",
            params={"type": "ws", "security": "tls", "fm": "1", "fp": "unsafe"},
            remark="Special parameter node",
            country_code="DE",
        )

        # Generate country nodes to trigger country files (>= 5 for DE)
        de_nodes = [
            ProxyNode(
                scheme="vmess",
                address=f"10.0.0.{i}",
                port=443,
                credential=f"uuid-{i}",
                remark=f"DE Node {i}",
                country_code="DE",
            )
            for i in range(5)
        ]

        all_nodes = [public_verified, public_iran, special_node] + de_nodes
        json_cfgs = [{"outbounds": [{"protocol": "vless", "tag": "test-json"}]}]
        stats = [
            SourceStats(
                source_id="src-test",
                name="Test Source",
                url="http://test.url",
                success=True,
                status_code=200,
                elapsed_ms=120.0,
                raw_count=len(all_nodes),
                unique_count=len(all_nodes),
            )
        ]

        counts = publish_subscriptions(
            nodes=all_nodes,
            json_configs=json_cfgs,
            stats=stats,
            output_dir=str(self.output_dir),
            update_docs=False,
        )

        # Verify Core Files
        self.assertTrue((self.output_dir / "verified.txt").exists())
        self.assertTrue((self.output_dir / "verified_base64.txt").exists())
        self.assertTrue((self.output_dir / "all.txt").exists())
        self.assertTrue((self.output_dir / "all_base64.txt").exists())
        self.assertTrue((self.output_dir / "top100.txt").exists())
        self.assertTrue((self.output_dir / "lite500.txt").exists())

        # Verify Categories
        self.assertTrue((self.output_dir / "categories" / "pattn-special.txt").exists())
        self.assertTrue((self.output_dir / "categories" / "iran-optimized.txt").exists())
        self.assertTrue((self.output_dir / "categories" / "reality.txt").exists())
        self.assertTrue((self.output_dir / "categories" / "serverless-fragment.json").exists())

        # Verify Protocols
        self.assertTrue((self.output_dir / "protocols" / "vless.txt").exists())
        self.assertTrue((self.output_dir / "protocols" / "vmess.txt").exists())
        self.assertTrue((self.output_dir / "protocols" / "trojan.txt").exists())

        # Verify Country Grouping
        self.assertTrue((self.output_dir / "countries" / "de.txt").exists())

        # Verify Plaintext Profile Headers
        ver_text = (self.output_dir / "verified.txt").read_text(encoding="utf-8")
        self.assertIn("#profile-title: base64:", ver_text)
        self.assertIn("#profile-update-interval: 6", ver_text)
        self.assertIn("#subscription-userinfo:", ver_text)


class TestFetcherAndLoader(unittest.TestCase):
    def test_load_sources_public(self):
        # Test loading sources.json in isolation from dynamic discovered sources
        sources = load_sources(
            sources_path=str(BASE_DIR / "sources.json"),
            discovered_sources_path="non_existent_discovered.json",
        )
        self.assertEqual(len(sources), 60)

    @patch("src.fetcher._fetch_url")
    def test_fetch_source_success_and_error(self, mock_fetch):
        # 1. Success case
        test_uri = "vless://test-uuid@1.2.3.4:443?security=tls#TestNode"
        mock_fetch.return_value = (200, test_uri)
        src = SourceConfig(id="test-ok", name="OK", url="http://test.url", verified_upstream=True)
        nodes, jsons, stats = fetch_source(src)

        self.assertTrue(stats.success)
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(stats.raw_count, 1)

        # 2. Failure case
        mock_fetch.side_effect = urllib.error.URLError("Connection refused")
        src_fail = SourceConfig(id="test-err", name="Err", url="http://err.url")
        f_nodes, f_jsons, f_stats = fetch_source(src_fail)

        self.assertFalse(f_stats.success)
        self.assertIsNone(f_stats.status_code)
        self.assertIn("Connection refused", f_stats.error or "")
        self.assertEqual(len(f_nodes), 0)


class TestMainPipeline(unittest.TestCase):
    def test_cli_rejects_synthesis_and_core_switches(self):
        for flag in ("--pattn-variants", "--no-pattn-variants", "--use-xray", "--no-use-xray"):
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    build_arg_parser().parse_args([flag])
                self.assertEqual(result.exception.code, 2)

    @patch("src.main.load_sources", return_value=[])
    @patch("src.main.fetch_all_sources")
    def test_main_cli_execution(self, mock_fetch, _mock_sources):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        out_path = Path(temp_dir.name) / "cli_subs"

        node = ProxyNode(
            scheme="vless",
            address="cli.example.com",
            port=443,
            credential="cli-uuid",
            params={"type": "ws", "security": "tls", "sni": "cli.example.com"},
            remark="CLI Test",
            country_code="US",
            is_verified_source=True,
        )
        stats = [
            SourceStats(
                source_id="src-cli",
                name="CLI Src",
                url="http://cli",
                success=True,
                status_code=200,
            )
        ]
        mock_fetch.return_value = ([node], [], stats)

        exit_code = main([
            "--sources", str(BASE_DIR / "sources.json"),
            "--output-dir", str(out_path),
            "--no-healthcheck",
            "--no-docs",
            "--max-workers", "2",
        ])

        self.assertEqual(exit_code, 0)
        self.assertTrue((out_path / "all.txt").exists())
        self.assertTrue((out_path / "categories" / "pattn-special.txt").exists())
        from src.parsers import extract_nodes_from_content
        published, _ = extract_nodes_from_content((out_path / "all.txt").read_text(encoding="utf-8"), SourceConfig("fixture", "Fixture", "https://example.com"))
        self.assertEqual(len(published), 1)
        self.assertEqual((published[0].address, published[0].port, published[0].credential), ("cli.example.com", 443, "cli-uuid"))
        self.assertEqual(published[0].params, node.params)

        temp_dir.cleanup()

    def test_main_optional_healthcheck_remains_socket_only_without_changing_connections(self):
        node = ProxyNode("vless", "socket.example", 8443, " custom ",
                         params={"type": "ws", "security": "tls", "host": "cdn.example", "path": " /x/ ", "flow": ""})
        attempts = []

        def fixture_socket(candidate, timeout):
            attempts.append((candidate.address, candidate.port, candidate.credential, dict(candidate.params)))
            return True, 12.0

        with tempfile.TemporaryDirectory() as scratch, \
             patch("src.main.load_sources", return_value=[]), \
             patch("src.main.fetch_all_sources", return_value=([node], [], [])), \
             patch("src.healthcheck.probe_node_socket", side_effect=fixture_socket):
            self.assertEqual(main(["--output-dir", scratch, "--healthcheck", "--no-docs"]), 0)
            self.assertEqual(attempts, [("socket.example", 8443, " custom ",
                {"type": "ws", "security": "tls", "host": "cdn.example", "path": " /x/ ", "flow": ""})])
            from src.parsers import extract_nodes_from_content
            published, _ = extract_nodes_from_content((Path(scratch) / "all.txt").read_text(encoding="utf-8"), SourceConfig("fixture", "Fixture", "https://example.com"))
            self.assertEqual(len(published), 1)
            self.assertEqual(published[0].semantic_fingerprint(), node.semantic_fingerprint())
