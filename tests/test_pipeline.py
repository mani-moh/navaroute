from __future__ import annotations

import base64
import contextlib
import io
import json
import socket
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.categorizer import publish_subscriptions
from src.fetcher import fetch_source, load_sources
from src.healthcheck import (
    build_xray_outbound,
    check_nodes_health,
    probe_node_socket,
    probe_node_xray,
)
from src.main import build_arg_parser, main
from src.models import ProxyNode, SourceConfig, SourceStats

BASE_DIR = Path(__file__).resolve().parent.parent
CORE_FIXTURE = str(Path(tempfile.gettempdir()) / "navaroute-nonexistent-fixture-core")


class TestHealthcheck(unittest.TestCase):
    def setUp(self):
        from src.network import clear_dns_cache
        clear_dns_cache()

    def test_socket_rejects_private_without_connect_and_records_real_kind(self):
        from src import healthcheck as hc
        for address in ("127.0.0.1", "192.168.1.1", "192.0.2.1", "::1"):
            with self.subTest(address=address), patch("src.network.socket.socket", side_effect=AssertionError("private connect")):
                self.assertEqual(probe_node_socket(ProxyNode("trojan", address, 443, "public")), (False, None))
        nodes = [ProxyNode("trojan", "1.1.1.1", 443, "public", passed_healthcheck=True, latency_ms=10),
                 ProxyNode("trojan", "8.8.8.8", 443, "other", passed_healthcheck=True, latency_ms=10)]
        with patch.object(hc, "probe_node_socket", return_value=(False, None)):
            check_nodes_health(nodes, max_nodes=1)
        self.assertEqual(nodes[0].healthcheck_kind, "socket")
        self.assertIsNotNone(nodes[0].healthchecked_at)
        self.assertFalse(nodes[1].passed_healthcheck)
        self.assertEqual(nodes[1].healthcheck_kind, "untested")
        self.assertIsNone(nodes[1].latency_ms)

    def test_healthcheck_sample_keeps_priority_and_rotates_the_rest(self):
        from src.healthcheck import select_healthcheck_sample
        priority = ProxyNode("trojan", "1.1.1.1", 443, "priority", is_verified_source=True)
        rest = [ProxyNode(scheme, "1.1.1.1", 443, f"{scheme}-{i}")
                for scheme in ("vless", "vmess") for i in range(6)]
        nodes = rest + [priority]
        sample = select_healthcheck_sample(nodes, 4, sample_seed=0)
        self.assertEqual(sample[0], priority)
        self.assertEqual({n.scheme for n in sample[1:]}, {"vless", "vmess"})
        # A different seed picks a different slice of the same population.
        rotated = select_healthcheck_sample(nodes, 4, sample_seed=3)
        self.assertEqual(rotated[0], priority)
        self.assertNotEqual([n.credential for n in sample[1:]], [n.credential for n in rotated[1:]])
        # Single-protocol, seed 0: the old take-the-first order is kept.
        single = [ProxyNode("vless", "1.1.1.1", 443, f"n{i}") for i in range(5)]
        self.assertEqual([n.credential for n in select_healthcheck_sample(single, 2, sample_seed=0)], ["n0", "n1"])
        self.assertEqual(select_healthcheck_sample(single, 10), single)
        with self.assertRaises(ValueError):
            select_healthcheck_sample(single, 2, sample_seed=-1)

    def test_faithful_trojan_tls_vmess_grpc_and_supported_settings(self):
        trojan = build_xray_outbound(ProxyNode("trojan", "public.example", 443, "public"))
        self.assertEqual(trojan["streamSettings"]["security"], "tls")
        self.assertEqual(trojan["streamSettings"]["tlsSettings"]["serverName"], "public.example")
        vmess = build_xray_outbound(ProxyNode("vmess", "1.1.1.1", 443, "uuid", params={
            "type": "grpc", "path": "service", "security": "tls", "sni": "public.example", "aid": "0",
            "scy": "auto", "headerType": "none", "v": "2", "alpn": ["h2", "http/1.1"]}))
        self.assertEqual(vmess["streamSettings"]["grpcSettings"], {"serviceName": "service"})
        self.assertEqual(vmess["streamSettings"]["tlsSettings"]["alpn"], ["h2", "http/1.1"])
        ws = build_xray_outbound(ProxyNode("vless", "1.1.1.1", 443, "uuid", params={
            "type": "ws", "security": "tls", "host": "cdn.example", "sni": "tls.example", "path": "/ws", "flow": ""}))
        self.assertEqual(ws["streamSettings"]["wsSettings"], {"path": "/ws", "headers": {"Host": "cdn.example"}})
        self.assertEqual(ws["streamSettings"]["tlsSettings"]["serverName"], "tls.example")

    def test_unsupported_complete_settings_never_drop_into_pass(self):
        for params in ({"fm": "1"}, {"cs": "1"}, {"dialMode": "auto"}, {"plugin": "obfs-local"},
                       {"unknown": {"nested": True}}, {"type": "xhttp", "mode": "auto"},
                       {"headerType": "http"}, {"aid": "oops"}, {"security": "tls", "alpn": {"x": "h2"}},
                       {"type": "tcp", "path": "/ignored"}, {"type": "grpc", "path": "one", "serviceName": "two"}):
            with self.subTest(params=params):
                self.assertIsNone(build_xray_outbound(ProxyNode("vmess", "1.1.1.1", 443, "public", params=params)))
        self.assertIsNone(build_xray_outbound(ProxyNode("hysteria2", "1.1.1.1", 443, "public")))
        with self.assertRaises(ValueError):
            ProxyNode("vmess", "1.1.1.1", 443, "public", params={"type": ["ws", "grpc"]})
        node = ProxyNode("vmess", "1.1.1.1", 443, "public")
        node.params = {"type": ["ws", "grpc"]}
        self.assertIsNone(build_xray_outbound(node))

    def test_manual_mode_missing_binary_never_falls_back(self):
        from src import healthcheck as hc
        node = ProxyNode("trojan", "1.1.1.1", 443, "public")
        with patch.object(hc, "probe_node_socket", side_effect=AssertionError("fallback forbidden")):
            with self.assertRaises(hc.CoreInfrastructureError):
                check_nodes_health([node], use_xray=True)
            with patch.object(hc.subprocess, "run", side_effect=OSError("missing fixture core")), self.assertRaises(hc.CoreInfrastructureError):
                probe_node_xray(node, CORE_FIXTURE)

    def test_relative_core_path_never_uses_path_or_resolves_nodes(self):
        from src import healthcheck as hc
        node = ProxyNode("trojan", "public.example", 443, "public")
        with patch.object(hc, "prepare_public_outbound", side_effect=AssertionError("reject core before DNS")), \
             patch.object(hc.subprocess, "Popen", side_effect=AssertionError("PATH lookup forbidden")):
            with self.assertRaises(hc.CoreInfrastructureError):
                probe_node_xray(node, "xray")

    def test_unsupported_filtered_before_budget_and_round_intersection(self):
        from src import healthcheck as hc
        nodes = [ProxyNode("hysteria2", "1.1.1.1", 443, "public"),
                 ProxyNode("trojan", "1.1.1.1", 443, "one"), ProxyNode("vmess", "8.8.8.8", 443, "two")]
        with patch.object(hc, "prepare_public_outbound", side_effect=lambda n, t: build_xray_outbound(n)), \
             patch.object(hc, "probe_batch_xray", side_effect=[hc.BatchResult({0: 120., 1: 80.}, {0, 1}, set()),
                                                           hc.BatchResult({1: 90.}, {0, 1}, set())]):
            summary = check_nodes_health(nodes, max_nodes=2, use_xray=True, xray_bin=CORE_FIXTURE, rounds=2)
        self.assertEqual(summary["tested"], 2)
        self.assertEqual(summary["unsupported"], 1)
        self.assertEqual(nodes[0].healthcheck_kind, "untested")
        self.assertFalse(nodes[1].passed_healthcheck)
        self.assertTrue(nodes[2].passed_healthcheck)
        self.assertEqual(nodes[2].latency_ms, 85.)

    def test_config_failure_after_a_good_round_is_infrastructure_not_failed_probe(self):
        from src import healthcheck as hc
        node = ProxyNode("trojan", "1.1.1.1", 443, "public")
        with patch.object(hc, "probe_batch_xray", side_effect=[hc.BatchResult({0: 10.}, {0}, set()), hc.BatchResult(unsupported={0})]):
            with self.assertRaises(hc.CoreInfrastructureError):
                check_nodes_health([node], use_xray=True, xray_bin=CORE_FIXTURE, rounds=2)

    def test_transport_failure_before_connect_is_infrastructure_not_node_failure(self):
        from src import healthcheck as hc
        from src.network import NetworkError
        with patch("src.network._resolve", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]), \
             patch("src.network._connect", side_effect=NetworkError("owned proxy connection unavailable")):
            with self.assertRaises(hc.CoreInfrastructureError):
                hc._probe_http_via_local_proxy(25000)

    def test_default_cert_context_and_total_read_deadline_are_used_through_connect(self):
        from src import healthcheck as hc
        from tests.test_network import WireSocket
        import ssl
        real_context = ssl.create_default_context()
        clock = [100.0]
        def wrap(context, transport, **kw):
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            self.assertEqual(kw["server_hostname"], "cp.cloudflare.com")
            transport.response = b"HTTP/1.1 204 No Content\r\n\r\n"
            return transport
        sock = WireSocket(b"HTTP/1.1 200 Connection established\r\n\r\n", clock=clock)
        with patch("src.network._resolve", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]), \
             patch("src.network._connect", return_value=sock), patch("src.network.ssl.create_default_context", return_value=real_context), \
             patch.object(ssl.SSLContext, "wrap_socket", new=wrap), patch("src.network.time.monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(hc._probe_http_via_local_proxy(25000, timeout=.1), (False, None))

    def test_public_socket_positive_uses_validated_numeric_peer_without_tls_claim(self):
        from tests.test_network import WireSocket
        class SocketFixture(WireSocket):
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
        fixture = SocketFixture(b"", peer=("1.1.1.1", 443))
        with patch("src.network.socket.socket", return_value=fixture):
            passed, latency = probe_node_socket(ProxyNode("trojan", "1.1.1.1", 443, "public"))
        self.assertTrue(passed)
        self.assertIsNotNone(latency)
        self.assertEqual(fixture.connected, ("1.1.1.1", 443))
        self.assertTrue(fixture.closed)


class TestHealthcheckOutbound(unittest.TestCase):
    def test_vless_flow_and_encryption_validation(self):
        for params in ({"encryption": "aes-128-gcm"}, {"flow": "xtls-rprx-origin"},
                       {"flow": "xtls-rprx-vision", "type": "ws", "security": "tls"},
                       {"flow": "xtls-rprx-vision", "type": "tcp", "security": "none"}):
            with self.subTest(params=params):
                self.assertIsNone(build_xray_outbound(ProxyNode("vless", "1.1.1.1", 443, "uuid", params=params)))
        vision = build_xray_outbound(ProxyNode("vless", "1.1.1.1", 443, "uuid",
                                               params={"flow": "xtls-rprx-vision", "type": "tcp", "security": "tls"}))
        self.assertEqual(vision["settings"]["vnext"][0]["users"][0]["flow"], "xtls-rprx-vision")

    def test_vmess_scy_and_ss_transport_validation(self):
        self.assertIsNone(build_xray_outbound(ProxyNode("vmess", "1.1.1.1", 443, "uuid", params={"scy": "aes-256-gcm"})))
        self.assertIsNone(build_xray_outbound(ProxyNode("ss", "1.1.1.1", 443, "aes-256-gcm:pass", params={"type": "ws"})))
        self.assertIsNone(build_xray_outbound(ProxyNode("ss", "1.1.1.1", 443, "aes-256-gcm:pass", params={"security": "tls"})))

    def test_shadowsocks_methods_and_missing_password(self):
        ss = build_xray_outbound(ProxyNode("ss", "1.1.1.1", 443, "aes-256-gcm:secret"))
        self.assertEqual(ss["protocol"], "shadowsocks")
        self.assertEqual(ss["settings"]["servers"][0]["method"], "aes-256-gcm")
        for credential in ("rc4-md5:secret", "aes-256-gcm:", "aes-256-gcm"):
            with self.subTest(credential=credential):
                self.assertIsNone(build_xray_outbound(ProxyNode("ss", "1.1.1.1", 443, credential)))

    def test_sni_fingerprint_and_allow_insecure(self):
        self.assertIsNone(build_xray_outbound(ProxyNode("trojan", "1.1.1.1", 443, "public",
                                                        params={"sni": "not a hostname!"})))
        reality = build_xray_outbound(ProxyNode("vless", "1.1.1.1", 443, "uuid", params={
            "security": "reality", "pbk": "public-key", "sid": "short", "spx": "/", "fp": "firefox"}))
        self.assertEqual(reality["streamSettings"]["realitySettings"]["publicKey"], "public-key")
        self.assertEqual(reality["streamSettings"]["realitySettings"]["fingerprint"], "firefox")
        self.assertIsNone(build_xray_outbound(ProxyNode("vless", "1.1.1.1", 443, "uuid",
                                                        params={"security": "reality"})))
        self.assertIsNone(build_xray_outbound(ProxyNode("vmess", "1.1.1.1", 443, "uuid",
                                                        params={"security": "reality", "pbk": "public-key"})))
        self.assertIsNone(build_xray_outbound(ProxyNode("trojan", "1.1.1.1", 443, "public",
                                                        params={"allowInsecure": "maybe"})))
        insecure = build_xray_outbound(ProxyNode("trojan", "1.1.1.1", 443, "public",
                                                 params={"allowInsecure": "true"}))
        self.assertTrue(insecure["streamSettings"]["tlsSettings"]["allowInsecure"])

    def test_ws_host_path_and_httpupgrade_settings(self):
        self.assertIsNone(build_xray_outbound(ProxyNode("vless", "1.1.1.1", 443, "uuid",
                                                        params={"type": "ws", "host": "bad\thost"})))
        self.assertIsNone(build_xray_outbound(ProxyNode("vless", "1.1.1.1", 443, "uuid",
                                                        params={"type": "ws", "path": "no-leading-slash"})))
        upgrade = build_xray_outbound(ProxyNode("vless", "1.1.1.1", 443, "uuid",
                                                params={"type": "httpupgrade", "host": "cdn.example", "path": "/up"}))
        self.assertEqual(upgrade["streamSettings"]["httpupgradeSettings"], {"path": "/up", "host": "cdn.example"})

    def test_grpc_mode_validation_and_multi(self):
        self.assertIsNone(build_xray_outbound(ProxyNode("vmess", "1.1.1.1", 443, "uuid",
                                                        params={"type": "grpc", "mode": "auto"})))
        self.assertIsNone(build_xray_outbound(ProxyNode("vmess", "1.1.1.1", 443, "uuid",
                                                        params={"type": "grpc", "serviceName": "bad\nname"})))
        multi = build_xray_outbound(ProxyNode("vmess", "1.1.1.1", 443, "uuid",
                                              params={"type": "grpc", "serviceName": "svc", "mode": "multi"}))
        self.assertTrue(multi["streamSettings"]["grpcSettings"]["multiMode"])

    def test_prepare_pins_httpupgrade_host_to_resolved_authority(self):
        from src import healthcheck as hc
        node = ProxyNode("vless", "public.example", 443, "uuid", params={"type": "httpupgrade", "path": "/up"})
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with patch.object(hc, "_resolve", return_value=answers):
            outbound = hc.prepare_public_outbound(node)
        self.assertEqual(outbound["streamSettings"]["httpupgradeSettings"]["host"], "public.example:443")
        self.assertEqual(outbound["settings"]["vnext"][0]["address"], "93.184.216.34")

    def test_wait_ports_and_stop_owned_branches(self):
        from src import healthcheck as hc
        exited = MagicMock()
        exited.poll.return_value = 0
        self.assertFalse(hc._wait_ports_listening([25001], exited, timeout=.1))
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        try:
            running = MagicMock()
            running.poll.return_value = None
            self.assertTrue(hc._wait_ports_listening([listener.getsockname()[1]], running, timeout=2))
            self.assertFalse(hc._wait_ports_listening([25001], running, timeout=.1))
        finally:
            listener.close()
        done = MagicMock()
        done.poll.return_value = 0
        hc._stop_owned(done)
        done.wait.assert_called_once_with(timeout=1.5)
        done.terminate.assert_not_called()
        stubborn = MagicMock()
        stubborn.poll.return_value = None
        stubborn.terminate.side_effect = OSError("synthetic terminate failure")
        hc._stop_owned(stubborn)
        stubborn.kill.assert_called_once()
        self.assertEqual(stubborn.wait.call_count, 1)

    def test_probe_rejects_unsupported_policy_endpoint(self):
        from src import healthcheck as hc
        with self.assertRaises(ValueError):
            hc._probe_http_via_local_proxy(25000, test_url="https://evil.example/generate_204")

    def test_run_xray_startup_and_mid_probe_exit_are_infrastructure(self):
        from src import healthcheck as hc
        node = ProxyNode("trojan", "1.1.1.1", 443, "public")
        outbound = build_xray_outbound(node)
        checked = MagicMock()
        checked.returncode = 0
        with patch.object(hc.subprocess, "run", return_value=checked), \
             patch.object(hc.subprocess, "Popen", side_effect=OSError("synthetic core spawn failure")), \
             self.assertRaises(hc.CoreInfrastructureError):
            hc._run_xray_items([(0, outbound)], CORE_FIXTURE, 5.0, hc.HEALTHCHECK_TEST_URLS[0], 1)
        proc = MagicMock()
        proc.poll.return_value = 0
        with patch.object(hc.subprocess, "run", return_value=checked), \
             patch.object(hc.subprocess, "Popen", return_value=proc), \
             patch.object(hc, "_wait_ports_listening", return_value=True), \
             patch.object(hc, "_probe_http_via_local_proxy", return_value=(True, 5.0)), \
             self.assertRaises(hc.CoreInfrastructureError):
            hc._run_xray_items([(0, outbound)], CORE_FIXTURE, 5.0, hc.HEALTHCHECK_TEST_URLS[0], 1)

    def test_batch_rejects_bad_url_and_prepared_none_is_unsupported(self):
        from src import healthcheck as hc
        node = ProxyNode("trojan", "1.1.1.1", 443, "public")
        with self.assertRaises(ValueError):
            hc.probe_batch_xray([node], CORE_FIXTURE, test_url="https://evil.example/generate_204")
        result = hc.probe_batch_xray([node], CORE_FIXTURE, prepared=[None])
        self.assertEqual(result.unsupported, {0})
        self.assertEqual(result.passed, {})

    def test_probe_node_xray_passthrough_and_sample_priority_cap(self):
        from src import healthcheck as hc
        node = ProxyNode("trojan", "1.1.1.1", 443, "public")
        with patch.object(hc, "probe_batch_xray", return_value=hc.BatchResult({0: 12.5}, {0}, set())):
            self.assertEqual(hc.probe_node_xray(node, CORE_FIXTURE), (True, 12.5))
        priority = [ProxyNode("trojan", "1.1.1.1", 443, f"p{i}", is_verified_source=True) for i in range(3)]
        self.assertEqual([n.credential for n in hc.select_healthcheck_sample(priority, 2)], ["p0", "p1"])

    def test_check_nodes_health_rejects_bad_seed_and_bad_probe_url(self):
        from src import healthcheck as hc
        with self.assertRaises(ValueError):
            hc.check_nodes_health([], sample_seed=-1)
        with self.assertRaises(ValueError):
            hc.check_nodes_health([], use_xray=True, xray_bin=CORE_FIXTURE,
                                  test_urls=["https://evil.example/generate_204"])

    def test_https_204_only_with_verified_context_and_no_proxy_env(self):
        from src import healthcheck as hc
        from tests.test_network import WireSocket
        # Reuse the socket-free wire boundary; real CONNECT/HTTP/TLS policy code runs.
        for response, want in ((b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n", True),
                               (b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n", False),
                               (b"HTTP/1.1 204 No Content\r\nContent-Length: 1\r\n\r\nx", False)):
            sock = WireSocket(b"HTTP/1.1 200 Connection established\r\n\r\n", ("127.0.0.1", 25000))
            context = MagicMock()
            def wrap(transport, **kwargs):
                transport.response = response
                return transport
            context.wrap_socket.side_effect = wrap
            with self.subTest(response=response), \
                 patch("src.network._resolve", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]), \
                 patch("src.network._connect", return_value=sock), patch("src.network.ssl.create_default_context", return_value=context):
                passed, latency = hc._probe_http_via_local_proxy(25000)
            self.assertEqual(passed, want)
            self.assertEqual(context.wrap_socket.call_args.kwargs["server_hostname"], "cp.cloudflare.com")

    def test_core_config_failure_isolates_peer_and_start_failure_cleans_owned_temp(self):
        from src import healthcheck as hc
        import subprocess
        nodes = [ProxyNode("trojan", "1.1.1.1", 443, "one"), ProxyNode("trojan", "8.8.8.8", 443, "two")]
        paths = []

        def validate(argv, **kwargs):
            path = Path(argv[-1])
            paths.append(path)
            config = json.loads(path.read_text())
            bad = any(ob.get("settings", {}).get("servers", [{}])[0].get("password") == "one" for ob in config["outbounds"])
            self.assertNotIn("NVIDIA_API_KEY", kwargs["env"])
            self.assertNotIn("GH_TOKEN", kwargs["env"])
            return subprocess.CompletedProcess(argv, int(bad))

        proc = MagicMock()
        proc.poll.return_value = None
        with patch.object(hc, "prepare_public_outbound", side_effect=lambda n, t: build_xray_outbound(n)), \
             patch.object(hc.subprocess, "run", side_effect=validate), patch.object(hc.subprocess, "Popen", return_value=proc), \
             patch.object(hc, "_wait_ports_listening", return_value=True), patch.object(hc, "_probe_http_via_local_proxy", return_value=(True, 10.)):
            result = hc.probe_batch_xray(nodes, CORE_FIXTURE)
        self.assertEqual(result.passed, {1: 10.})
        self.assertEqual(result.unsupported, {0})
        self.assertTrue(all(not p.exists() for p in paths))
        proc.terminate.assert_called_once()
        proc.wait.assert_called()
        with patch.object(hc, "prepare_public_outbound", side_effect=lambda n, t: build_xray_outbound(n)), \
             patch.object(hc.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
             patch.object(hc.subprocess, "Popen", return_value=proc), patch.object(hc, "_wait_ports_listening", return_value=False):
            with self.assertRaises(hc.CoreInfrastructureError):
                hc.probe_batch_xray(nodes, CORE_FIXTURE)

    def test_pinned_dial_preserves_original_sni_host_and_fingerprint(self):
        from src import healthcheck as hc
        node = ProxyNode("trojan", "public.example", 443, "public", params={"type": "ws", "path": "/"})
        original = node.semantic_fingerprint()
        with patch("src.network.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]):
            outbound = hc.prepare_public_outbound(node)
        self.assertEqual(outbound["settings"]["servers"][0]["address"], "1.1.1.1")
        self.assertEqual(outbound["streamSettings"]["tlsSettings"]["serverName"], "public.example")
        self.assertEqual(outbound["streamSettings"]["wsSettings"]["headers"]["Host"], "public.example:443")
        self.assertEqual(node.semantic_fingerprint(), original)
        # The first phase cached 1.1.1.1; drop it so the rebinding
        # resolver below is actually consulted.
        from src.network import clear_dns_cache
        clear_dns_cache()
        with patch("src.network.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]), \
             patch.object(hc.subprocess, "Popen", side_effect=AssertionError("private core dial")):
            with self.assertRaises(ValueError):
                check_nodes_health([node], use_xray=True, xray_bin=CORE_FIXTURE)

    def test_grpc_pinning_preserves_tls_server_name_authority(self):
        from src import healthcheck as hc
        node = ProxyNode("vmess", "1.1.1.1", 443, "public", params={"type": "grpc", "path": "service", "security": "tls", "sni": "tls.example"})
        outbound = hc.prepare_public_outbound(node)
        self.assertEqual(outbound["streamSettings"]["grpcSettings"], {"serviceName": "service", "authority": "tls.example"})

    def test_captive_bodyless_204_with_illegal_body_is_not_pass(self):
        from src import healthcheck as hc
        from tests.test_network import WireSocket
        sock = WireSocket(b"HTTP/1.1 200 Connection established\r\n\r\n")
        context = MagicMock()
        def wrap(transport, **kw):
            transport.response = b"HTTP/1.1 204 No Content\r\n\r\ncaptive-sentinel"
            return transport
        context.wrap_socket.side_effect = wrap
        with patch("src.network._resolve", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]), \
             patch("src.network._connect", return_value=sock), patch("src.network.ssl.create_default_context", return_value=context):
            self.assertEqual(hc._probe_http_via_local_proxy(25000), (False, None))

    def test_invalid_destination_certificate_no_fallback_and_owned_cancel_kill_wait(self):
        from src import healthcheck as hc
        from tests.test_network import WireSocket
        import ssl
        import subprocess
        context = MagicMock()
        context.wrap_socket.side_effect = ssl.SSLCertVerificationError("private-sentinel")
        sock = WireSocket(b"HTTP/1.1 200 Connection established\r\n\r\n")
        with patch("src.network._resolve", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]), \
             patch("src.network._connect", return_value=sock), patch("src.network.ssl.create_default_context", return_value=context):
            self.assertEqual(hc._probe_http_via_local_proxy(25000), (False, None))
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired("supplied", 1), None]
        paths = []
        def popen(argv, **kw):
            paths.append(Path(argv[-1]))
            return proc
        node = ProxyNode("trojan", "1.1.1.1", 443, "public")
        with patch.object(hc.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
             patch.object(hc.subprocess, "Popen", side_effect=popen), \
             patch.object(hc, "_wait_ports_listening", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                hc.probe_batch_xray([node], CORE_FIXTURE)
        proc.kill.assert_called_once()
        self.assertEqual(proc.wait.call_count, 2)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_owned_termination_error_still_kills_and_waits(self):
        from src import healthcheck as hc
        proc = MagicMock()
        proc.poll.return_value = None
        proc.terminate.side_effect = OSError("terminated concurrently")
        hc._stop_owned(proc)
        proc.kill.assert_called_once()
        proc.wait.assert_called_once()


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

        publish_subscriptions(
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
        self.assertEqual(json.loads((self.output_dir / "categories" / "serverless-fragment.json").read_text()), [])

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
        self.assertNotIn("#subscription-userinfo:", ver_text)


class TestFetcherAndLoader(unittest.TestCase):
    def test_load_sources_public(self):
        # Test loading the unified sources.json in isolation (no legacy
        # second file): 60 curated + 92 discovered sources.
        sources = load_sources(
            sources_path=str(BASE_DIR / "sources.json"),
            discovered_sources_path="non_existent_discovered.json",
        )
        self.assertEqual(len(sources), 152)

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

    @patch("src.main.load_sources", return_value=[SourceConfig("fixture", "Fixture", "https://example.com")])
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
             patch("src.main.load_sources", return_value=[SourceConfig("fixture", "Fixture", "https://example.com")]), \
             patch("src.main.fetch_all_sources", return_value=([node], [], [SourceStats("fixture", "Fixture", "https://example.com", success=True, raw_count=1)])), \
             patch("src.healthcheck.probe_node_socket", side_effect=fixture_socket):
            self.assertEqual(main(["--output-dir", scratch, "--healthcheck", "--no-docs"]), 0)
            self.assertEqual(attempts, [("socket.example", 8443, " custom ",
                {"type": "ws", "security": "tls", "host": "cdn.example", "path": " /x/ ", "flow": ""})])
            from src.parsers import extract_nodes_from_content
            published, _ = extract_nodes_from_content((Path(scratch) / "all.txt").read_text(encoding="utf-8"), SourceConfig("fixture", "Fixture", "https://example.com"))
            self.assertEqual(len(published), 1)
            self.assertEqual(published[0].semantic_fingerprint(), node.semantic_fingerprint())

    def test_requested_discovery_failure_is_nonzero_and_never_publishes(self):
        from src.discovery.nemotron_client import DiscoveryAPIError
        source = SourceConfig("fixture", "Fixture", "https://example.com")
        node = ProxyNode("trojan", "public.example", 443, "public")
        stats = SourceStats("fixture", "Fixture", "https://example.com", success=True, raw_count=1)
        for flag in ("--discover", "--discover-only"):
            for failure in (DiscoveryAPIError("https://secret.invalid/?token=private"), OSError("private persistence"), ValueError("private rejected registry")):
                output = io.StringIO()
                with self.subTest(flag=flag, failure=type(failure).__name__), tempfile.TemporaryDirectory() as scratch, \
                     patch("src.discovery.agent.load_local_dotenv"), patch("src.discovery.agent.DiscoveryAgent") as agent, \
                     patch("src.main.load_sources", return_value=[source]), \
                     patch("src.main.fetch_all_sources", return_value=([node], [], [stats])), \
                     contextlib.redirect_stdout(output):
                    agent.return_value.run_cycle.side_effect = failure
                    code = main([flag, "--output-dir", str(Path(scratch) / "subs"), "--no-docs", "--no-healthcheck"])
                    self.assertNotEqual(code, 0)
                    self.assertFalse((Path(scratch) / "subs").exists())
                    self.assertNotIn("private", output.getvalue())
                    self.assertNotIn("secret.invalid", output.getvalue())

    def test_registry_fetch_empty_yield_and_persistence_fail_nonzero_without_publication(self):
        source = SourceConfig("fixture", "Fixture", "https://example.com")
        node = ProxyNode("trojan", "public.example", 443, "public")
        good = SourceStats("fixture", "Fixture", "https://example.com", success=True, raw_count=1)
        cases = [(ValueError("private registry"), None),
                 ([source], ValueError("private rejected input")),
                 ([source], ([], [], [SourceStats("fixture", "Fixture", "https://example.com")])),
                 ([source], ([], [], [SourceStats("fixture", "Fixture", "https://example.com", success=True)])),
                 ([source], ([node], [], [good]))]
        for loaded, fetched in cases:
            with self.subTest(loaded=type(loaded).__name__, fetched=type(fetched).__name__), tempfile.TemporaryDirectory() as scratch:
                path = Path(scratch) / "subs"
                path.mkdir()
                (path / "all.txt").write_bytes(b"complete previous generation")
                output = io.StringIO()
                with patch("src.main.load_sources", **({"side_effect": loaded} if isinstance(loaded, Exception) else {"return_value": loaded})), \
                     patch("src.main.fetch_all_sources", **({"side_effect": fetched} if isinstance(fetched, Exception) else {"return_value": fetched})), \
                     patch("src.categorizer.atomic_write_text", side_effect=OSError("private persistence failure"), create=True), \
                     contextlib.redirect_stdout(output):
                    self.assertNotEqual(main(["--output-dir", str(path), "--no-healthcheck", "--no-docs"]), 0)
                self.assertEqual((path / "all.txt").read_bytes(), b"complete previous generation")
                self.assertNotIn("private", output.getvalue())

    def test_failure_message_identifies_safe_function_and_line_without_exception_text(self):
        with tempfile.TemporaryDirectory() as scratch, patch("src.main.load_sources", return_value=[SourceConfig("fixture", "Fixture", "https://example.com")]), \
             patch("src.main.fetch_all_sources", side_effect=ValueError("private rejected input")), \
             contextlib.redirect_stdout(output := io.StringIO()):
            self.assertNotEqual(main(["--output-dir", scratch, "--no-healthcheck", "--no-docs"]), 0)
        self.assertRegex(output.getvalue(), r"Pipeline failed \(ValueError at <[^\r\n>]+>:[0-9]+\); publication stopped\.")
        self.assertNotIn("private rejected input", output.getvalue())

    def test_partial_source_failure_keeps_valid_peers_and_truthful_empty_reachable(self):
        with tempfile.TemporaryDirectory() as scratch, \
             patch("src.main.load_sources", return_value=[SourceConfig("fixture", "Fixture", "https://example.com")]), \
             patch("src.main.fetch_all_sources", return_value=([ProxyNode("trojan", "public.example", 443, "public", is_verified_source=True)], [],
                  [SourceStats("good", "Good", "https://example.com", success=True, raw_count=1), SourceStats("bad", "Bad", "https://example.com")])), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--output-dir", scratch, "--no-docs", "--no-healthcheck"]), 0)
            self.assertEqual((Path(scratch) / "reachable_base64.txt").read_bytes(), b"")

    def test_main_never_attempts_udp_protocols_using_tcp_healthchecks(self):
        from datetime import datetime, timezone
        observed = datetime.now(timezone.utc).isoformat()
        nodes = [ProxyNode("trojan", "public.example", 443, "public"),
                 ProxyNode("hysteria2", "udp.example", 443, "public", passed_healthcheck=True, latency_ms=1,
                           healthcheck_kind="socket", healthchecked_at=observed),
                 ProxyNode("tuic", "udp.example", 443, "user:public", passed_healthcheck=True, latency_ms=1,
                           healthcheck_kind="socket", healthchecked_at=observed)]
        def socket_fixture(node, timeout):
            if node.scheme != "trojan":
                raise AssertionError("UDP must not be tested with TCP")
            return True, 5.0
        with tempfile.TemporaryDirectory() as scratch, \
             patch("src.main.load_sources", return_value=[SourceConfig("fixture", "Fixture", "https://example.com")]), \
             patch("src.main.fetch_all_sources", return_value=(nodes, [], [SourceStats("fixture", "Fixture", "https://example.com", success=True, raw_count=3)])), \
             patch("src.healthcheck.probe_node_socket", side_effect=socket_fixture), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--output-dir", scratch, "--no-docs"]), 0)
            self.assertIn("1 nodes responsive", output.getvalue())
            from src.parsers import parse_proxy_uri
            reachable = base64.b64decode((Path(scratch) / "reachable_base64.txt").read_bytes()).decode().splitlines()
            self.assertEqual([parse_proxy_uri(uri).scheme for uri in reachable], ["trojan"])

    def test_main_all_socket_probes_failed_still_emits_only_accepted_aggregate(self):
        node = ProxyNode("trojan", "public.example", 443, "public", is_verified_source=True)
        with tempfile.TemporaryDirectory() as scratch, \
             patch("src.main.load_sources", return_value=[SourceConfig("fixture", "Fixture", "https://example.com")]), \
             patch("src.main.fetch_all_sources", return_value=([node], [], [SourceStats("fixture", "Fixture", "https://example.com", success=True, raw_count=1)])), \
             patch("src.healthcheck.probe_node_socket", return_value=(False, None)), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--output-dir", scratch, "--no-docs"]), 0)
            self.assertTrue((Path(scratch) / "all_base64.txt").read_bytes())
            for name in ("reachable", "verified", "top100"):
                self.assertEqual((Path(scratch) / f"{name}_base64.txt").read_bytes(), b"")
