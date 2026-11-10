from __future__ import annotations

import base64
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
from src.main import main
from src.models import ProxyNode, SourceConfig, SourceStats
from src.transform_pattn import (
    FAST_AND_FURIOUS_CIPHERS,
    FRAGMENT_FM_JSON,
    generate_pattn_variants,
)

BASE_DIR = Path(__file__).resolve().parent.parent


class TestPattnVariants(unittest.TestCase):
    def setUp(self):
        self.base_vless = ProxyNode(
            scheme="vless",
            address="cloudflare-cdn.com",
            port=443,
            credential="vless-uuid-1234",
            params={
                "type": "ws",
                "security": "tls",
                "sni": "my-worker.example.org",
                "host": "my-worker.example.org",
                "path": "/ws-path",
            },
            remark="Base VLESS DE",
            country_code="DE",
            is_verified_source=True,
            passed_healthcheck=True,
            latency_ms=45.0,
        )

        self.base_trojan = ProxyNode(
            scheme="trojan",
            address="trojan.example.com",
            port=443,
            credential="secret-trojan-pass",
            params={
                "type": "grpc",
                "security": "tls",
                "sni": "grpc.example.com",
                "serviceName": "trojan-grpc",
            },
            remark="Base Trojan US",
            country_code="US",
            is_verified_source=True,
        )

    def test_generate_pattn_variants_creates_three_variants(self):
        variants = generate_pattn_variants([self.base_vless], max_source_nodes=10)
        self.assertEqual(len(variants), 3)

        v_fnf = next(v for v in variants if "[PattN F&F]" in v.remark)
        v_oob = next(v for v in variants if "[PattN IPv6-OOB]" in v.remark)
        v_spoof = next(v for v in variants if "[PattN SNI-Spoof]" in v.remark)

        # 1. Fast & Furious Variant
        self.assertEqual(v_fnf.address, "188.114.98.0")
        self.assertEqual(v_fnf.port, 443)
        self.assertEqual(v_fnf.params.get("fp"), "unsafe")
        self.assertEqual(v_fnf.params.get("cs"), FAST_AND_FURIOUS_CIPHERS)
        self.assertEqual(v_fnf.params.get("fm"), FRAGMENT_FM_JSON)
        self.assertEqual(v_fnf.params.get("sni"), "my-worker.example.org")
        self.assertEqual(v_fnf.params.get("host"), "my-worker.example.org")
        self.assertTrue(v_fnf.is_pattn_special())
        self.assertIn("pattn", v_fnf.tags)
        self.assertIn("pattn_variant", v_fnf.tags)

        # Rebuilt URI check
        uri_fnf = v_fnf.to_uri(include_formatted_remark=True)
        self.assertIn("188.114.98.0:443", uri_fnf)
        self.assertIn("fp=unsafe", uri_fnf)
        self.assertIn("cs=", uri_fnf)
        self.assertIn("fm=", uri_fnf)
        self.assertIn("[PattN F&F]", uri_fnf)

        # 2. IPv6-OOB Variant
        self.assertIn("2a06:98c1:3120::7", v_oob.address)
        self.assertEqual(v_oob.port, 443)
        self.assertEqual(v_oob.params.get("dialMode"), "oob.0")
        self.assertEqual(v_oob.params.get("fp"), "hellochrome_120")
        self.assertEqual(v_oob.params.get("sni"), "my-worker.example.org")
        self.assertTrue(v_oob.is_pattn_special())

        uri_oob = v_oob.to_uri(include_formatted_remark=True)
        self.assertIn("[2a06:98c1:3120::7]:443", uri_oob)
        self.assertIn("dialMode=oob.0", uri_oob)
        self.assertIn("[PattN IPv6-OOB]", uri_oob)

        # 3. SNI-Spoof Variant
        self.assertEqual(v_spoof.address, "127.0.0.1")
        self.assertEqual(v_spoof.port, 40443)
        self.assertEqual(v_spoof.params.get("fp"), "chrome")
        self.assertEqual(v_spoof.params.get("sni"), "my-worker.example.org")
        self.assertTrue(v_spoof.is_pattn_special())

        uri_spoof = v_spoof.to_uri(include_formatted_remark=True)
        self.assertIn("127.0.0.1:40443", uri_spoof)
        self.assertIn("fp=chrome", uri_spoof)
        self.assertIn("[PattN SNI-Spoof]", uri_spoof)

    def test_pattn_variants_skip_existing_variants_and_ineligible_nodes(self):
        # A node already marked as variant should not spawn more variants
        variant_node = ProxyNode(
            scheme="vless",
            address="188.114.98.0",
            port=443,
            credential="uuid",
            tags={"pattn_variant"},
            remark="Node [PattN F&F]",
        )
        self.assertEqual(len(generate_pattn_variants([variant_node])), 0)

        # A node without TLS or without SNI/host
        ineligible_node = ProxyNode(
            scheme="ss",
            address="192.168.1.1",
            port=8388,
            credential="pass",
        )
        self.assertEqual(len(generate_pattn_variants([ineligible_node])), 0)


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

        pattn_variant = ProxyNode(
            scheme="vless",
            address="188.114.98.0",
            port=443,
            credential="pub-uuid-3",
            params={"type": "ws", "security": "tls", "fm": "1", "fp": "unsafe"},
            tags={"pattn", "pattn_variant"},
            remark="PattN F&F",
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

        all_nodes = [public_verified, public_iran, pattn_variant] + de_nodes
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
    @patch("src.main.fetch_all_sources")
    def test_main_cli_execution(self, mock_fetch):
        temp_dir = tempfile.TemporaryDirectory()
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
            "--pattn-variants",
            "--no-docs",
            "--max-workers", "2",
        ])

        self.assertEqual(exit_code, 0)
        self.assertTrue((out_path / "all.txt").exists())
        self.assertTrue((out_path / "categories" / "pattn-special.txt").exists())

        temp_dir.cleanup()

