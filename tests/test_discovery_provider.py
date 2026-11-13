"""OpenRouter provider fallback and DNS-retry behaviour (offline fixtures)."""
import json
import os
import socket
import unittest
from unittest.mock import patch

from src.discovery.nemotron_client import (
    DEFAULT_OPENROUTER_BASE_URL,
    DEFAULT_OPENROUTER_MODEL,
    NemotronClient,
)
from src.network import NetworkError, _resolve
from tests.test_discovery import OfflineDiscoveryTestCase, completion


class TestOpenRouterProvider(OfflineDiscoveryTestCase):
    def test_openrouter_only_key_selects_openrouter_by_default(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
        self.assertEqual(client.provider, "openrouter")
        self.assertEqual(client.base_url, DEFAULT_OPENROUTER_BASE_URL)
        self.assertEqual(client.model, DEFAULT_OPENROUTER_MODEL)
        self.assertTrue(client.is_available())

    def test_both_keys_select_openrouter_primary_with_nvidia_fallback(self):
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-synthetic", "OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
        self.assertEqual(client.provider, "openrouter")
        self.assertEqual(client.model, DEFAULT_OPENROUTER_MODEL)
        self.assertEqual(client.fallback_provider, "nvidia")
        self.assertEqual(client.fallback_api_key, "nvapi-synthetic")

    def test_explicit_provider_openrouter(self):
        client = NemotronClient(api_key="sk-or-v1-synthetic", provider="openrouter")
        self.assertEqual(client.provider, "openrouter")
        self.assertEqual(client.base_url, DEFAULT_OPENROUTER_BASE_URL)

    def test_unapproved_origin_still_rejected(self):
        for base in ("https://attacker.example/v1", "http://openrouter.ai/api/v1", "https://openrouter.ai.evil.example/v1"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                NemotronClient(api_key="synthetic", base_url=base, provider="openrouter")

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_openrouter_call_uses_openrouter_url_no_reasoning_effort(self, transport, _clock):
        transport.return_value = completion("openrouter result")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
            client.configure_cycle(max_attempts=5, deadline=30)
            self.assertEqual(client.complete("public query"), "openrouter result")
        self.assertEqual(transport.call_args_list[0].args[0], DEFAULT_OPENROUTER_BASE_URL + "/chat/completions")
        payload = json.loads(transport.call_args_list[0].kwargs["data"])
        self.assertEqual(payload["model"], DEFAULT_OPENROUTER_MODEL)
        self.assertNotIn("reasoning_effort", payload)
        self.assertEqual(transport.call_args_list[0].kwargs["headers"]["HTTP-Referer"], "https://github.com/mani-moh/navaroute")

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_nvidia_dns_failure_falls_back_to_openrouter(self, transport, _sleep, _clock):
        # Primary host fails on transport (DNS/connect), fallback host succeeds.
        def route(url, **kwargs):
            if "integrate.api.nvidia.com" in url:
                raise NetworkError("DNS resolution failed")
            return completion("fallback result")

        transport.side_effect = route
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-synthetic", "OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient(provider="nvidia")
            client.configure_cycle(max_attempts=10, deadline=30)
            self.assertEqual(client.complete("public query"), "fallback result")
        urls = [call.args[0] for call in transport.call_args_list]
        self.assertTrue(any("integrate.api.nvidia.com" in u for u in urls))
        self.assertTrue(any("openrouter.ai" in u for u in urls))
        # Transport failure must not burn every model on the dead host.
        self.assertLessEqual(sum("integrate.api.nvidia.com" in u for u in urls), 3)

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_openrouter_primary_falls_back_to_nvidia(self, transport, _sleep, _clock):
        # New default: OpenRouter first, NVIDIA as fallback.
        def route(url, **kwargs):
            if "openrouter.ai" in url:
                raise NetworkError("BrokenPipeError")
            return completion("nvidia result")

        transport.side_effect = route
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-synthetic", "OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
            self.assertEqual(client.provider, "openrouter")
            client.configure_cycle(max_attempts=10, deadline=30)
            self.assertEqual(client.complete("public query"), "nvidia result")
        urls = [call.args[0] for call in transport.call_args_list]
        self.assertTrue(any("openrouter.ai" in u for u in urls))
        self.assertTrue(any("integrate.api.nvidia.com" in u for u in urls))

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_no_fallback_without_openrouter_key(self, transport, _sleep, _clock):
        transport.side_effect = NetworkError("DNS resolution failed")
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-synthetic"}):
            client = NemotronClient()
            client.configure_cycle(max_attempts=5, deadline=30)
            with self.assertRaises(RuntimeError):
                client.complete("public query")
        self.assertTrue(all("integrate.api.nvidia.com" in c.args[0] for c in transport.call_args_list))

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_mixed_503_broken_pipe_and_404_falls_back_to_openrouter(self, transport, _sleep, _clock):
        # Real Nov 13 run 43398773141 log: 6x HTTP 503 BrokenPipe + 1x HTTP 404,
        # no DNS line. A 404 for one model must not block the other provider.
        # Here the primary fails on transport first; the fallback then succeeds.
        calls = {"n": 0}

        def mixed(url, **kwargs):
            if "openrouter.ai" in url:
                return completion("fallback result")
            calls["n"] += 1
            if calls["n"] <= 3:
                raise NetworkError("BrokenPipeError")
            return (404, b"model not found")

        transport.side_effect = mixed
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-synthetic", "OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient(provider="nvidia")
            client.configure_cycle(max_attempts=10, deadline=30)
            self.assertEqual(client.complete("public query"), "fallback result")
        urls = [call.args[0] for call in transport.call_args_list]
        self.assertTrue(any("openrouter.ai" in u for u in urls))

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_non_transport_failure_does_not_fall_back(self, transport, _clock):
        # A 404 is per-model, not per-host: fall through models on NVIDIA only.
        transport.side_effect = [(404, b""), completion("nvidia result")]
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-synthetic", "OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient(provider="nvidia")
            client.configure_cycle(max_attempts=5, deadline=30)
            self.assertEqual(client.complete("public query"), "nvidia result")
        self.assertTrue(all("integrate.api.nvidia.com" in c.args[0] for c in transport.call_args_list))


class TestDnsRetry(unittest.TestCase):
    def test_transient_dns_failure_is_retried_then_succeeds(self):
        calls = {"n": 0}

        def resolver(host, port, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                return None
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port))]

        with patch("src.network.socket.getaddrinfo", resolver), patch("src.network.time.sleep") as sleep:
            result = _resolve("public.example", 80, __import__("time").monotonic() + 30)
        self.assertEqual(result[0][4], ("8.8.8.8", 80))
        self.assertEqual(calls["n"], 3)
        self.assertEqual(sleep.call_count, 2)

    def test_mixed_private_dns_is_never_retried(self):
        def resolver(host, port, *args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port)),
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.1", port)),
            ]

        with patch("src.network.socket.getaddrinfo", resolver), patch("src.network.time.sleep") as sleep:
            with self.assertRaises(ValueError):
                _resolve("mixed.example", 80, __import__("time").monotonic() + 30)
        sleep.assert_not_called()

    def test_ipv4_is_preferred_over_ipv6(self):
        def resolver(host, port, *args, **kwargs):
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2606:4700:4700::1111", port)),
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port)),
            ]

        with patch("src.network.socket.getaddrinfo", resolver):
            result = _resolve("public.example", 80, __import__("time").monotonic() + 30)
        self.assertEqual(result[0][0], socket.AF_INET)
        self.assertEqual(result[0][4], ("8.8.8.8", 80))


if __name__ == "__main__":
    unittest.main()
