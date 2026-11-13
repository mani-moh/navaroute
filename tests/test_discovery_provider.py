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
        self.assertEqual(payload["reasoning"], {"effort": "high"})
        self.assertEqual(payload["max_tokens"], 4096)
        self.assertEqual(transport.call_args_list[0].kwargs["headers"]["HTTP-Referer"], "https://github.com/mani-moh/navaroute")

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_openrouter_keeps_nvidia_hosted_free_model(self, transport, _clock):
        # Regression: an earlier filter dropped every nvidia/* id on
        # OpenRouter, which wrongly excluded OpenRouter-hosted Nemotron
        # :free models. Only stepfun-ai/step-5-preview is NVIDIA-only.
        transport.return_value = completion("openrouter result")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient(model="nvidia/nemotron-3-ultra-550b-a55b:free", provider="openrouter")
            client.configure_cycle(max_attempts=5, deadline=30)
            self.assertEqual(client.complete("public query"), "openrouter result")
        payload = json.loads(transport.call_args_list[0].kwargs["data"])
        self.assertEqual(payload["model"], "nvidia/nemotron-3-ultra-550b-a55b:free")

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_router_fallback_attempt_carries_no_reasoning(self, transport, _sleep, _clock):
        transport.side_effect = [(404, b""), completion("router result")]
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
            client.configure_cycle(max_attempts=5, deadline=30)
            self.assertEqual(client.complete("public query"), "router result")
        payloads = [json.loads(call.kwargs["data"]) for call in transport.call_args_list]
        self.assertEqual([p["model"] for p in payloads], [DEFAULT_OPENROUTER_MODEL, "openrouter/free"])
        self.assertEqual(payloads[0]["reasoning"], {"effort": "high"})
        self.assertNotIn("reasoning", payloads[1])

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
    def test_openrouter_primary_does_not_fall_back_to_nvidia(self, transport, _sleep, _clock):
        # No cross-provider fallback FROM OpenRouter: its model list already
        # ends at the free router, and no NVIDIA model currently works
        # (stepfun-ai/step-5-preview 404s), so that attempt would only burn
        # a request on a 404.
        def route(url, **kwargs):
            if "openrouter.ai" in url:
                raise NetworkError("BrokenPipeError")
            return completion("nvidia result")

        transport.side_effect = route
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-synthetic", "OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
            self.assertEqual(client.provider, "openrouter")
            client.configure_cycle(max_attempts=10, deadline=30)
            with self.assertRaises(RuntimeError):
                client.complete("public query")
        urls = [call.args[0] for call in transport.call_args_list]
        self.assertTrue(all("openrouter.ai" in u for u in urls))
        self.assertFalse(any("integrate.api.nvidia.com" in u for u in urls))

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_openrouter_http_failure_on_both_models_does_not_fall_back_to_nvidia(self, transport, _sleep, _clock):
        # HTTP 503 on the primary model, then on the free router: still no
        # NVIDIA attempt.
        transport.side_effect = [(503, b"")] * 6
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-synthetic", "OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
            client.configure_cycle(max_attempts=10, deadline=30)
            with self.assertRaises(RuntimeError):
                client.complete("public query")
        urls = [call.args[0] for call in transport.call_args_list]
        self.assertTrue(all("openrouter.ai" in u for u in urls))
        models = [json.loads(call.kwargs["data"])["model"] for call in transport.call_args_list]
        self.assertEqual(models, [DEFAULT_OPENROUTER_MODEL] * 3 + ["openrouter/free"] * 3)

    @staticmethod
    def _null_content():
        # Real Nov 13 run 43469835592: HTTP 200 with null content — the
        # model spent its token budget on reasoning and returned nothing.
        return 200, json.dumps({"choices": [{"message": {"role": "assistant", "content": None}}]}).encode()

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_null_content_200_is_retried_on_same_model(self, transport, _sleep, _clock):
        transport.side_effect = [self._null_content(), completion("second try")]
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
            client.configure_cycle(max_attempts=10, deadline=30)
            self.assertEqual(client.complete("public query"), "second try")
        models = [json.loads(call.kwargs["data"])["model"] for call in transport.call_args_list]
        self.assertEqual(models, [DEFAULT_OPENROUTER_MODEL] * 2)
        self.assertEqual(client.api_requests_count, 2)

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_null_content_on_default_model_falls_back_to_router(self, transport, _sleep, _clock):
        transport.side_effect = [self._null_content()] * 3 + [completion("router result")]
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
            client.configure_cycle(max_attempts=10, deadline=30)
            self.assertEqual(client.complete("public query"), "router result")
        models = [json.loads(call.kwargs["data"])["model"] for call in transport.call_args_list]
        self.assertEqual(models, [DEFAULT_OPENROUTER_MODEL] * 3 + ["openrouter/free"])

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_persistent_null_content_degrades_to_empty_not_a_crash(self, transport, _sleep, _clock):
        # Every attempt empty on both models: complete() returns "" (the
        # agent skips that prompt) instead of killing the whole run.
        transport.side_effect = [self._null_content()] * 6
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
            client.configure_cycle(max_attempts=10, deadline=30)
            self.assertEqual(client.complete("public query"), "")
        models = [json.loads(call.kwargs["data"])["model"] for call in transport.call_args_list]
        self.assertEqual(models, [DEFAULT_OPENROUTER_MODEL] * 3 + ["openrouter/free"] * 3)
        self.assertEqual(client.api_requests_count, 6)

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_null_content_mixed_with_real_failure_still_raises(self, transport, _sleep, _clock):
        # Empty replies on the default model, then a real 503 on the router:
        # that is a provider failure, not a skipped prompt, so it raises.
        transport.side_effect = [self._null_content()] * 3 + [(503, b"")] * 3
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
            client.configure_cycle(max_attempts=10, deadline=30)
            with self.assertRaises(RuntimeError):
                client.complete("public query")

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_content_parts_list_is_joined(self, transport, _clock):
        parts = (200, json.dumps({"choices": [{"message": {"role": "assistant", "content": [
            {"type": "text", "text": "hello "}, {"type": "text", "text": "world"}]}}]}).encode())
        transport.return_value = parts
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
            client.configure_cycle(max_attempts=10, deadline=30)
            self.assertEqual(client.complete("public query"), "hello world")

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
    def setUp(self):
        from src.network import clear_dns_cache
        clear_dns_cache()

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
