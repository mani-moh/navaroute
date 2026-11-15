"""OpenRouter provider fallback and DNS-retry behaviour (offline fixtures)."""
import json
import os
import socket
import unittest
from unittest.mock import patch

from src.discovery.nemotron_client import (
    DEFAULT_OPENROUTER_BASE_URL,
    DEFAULT_OPENROUTER_MODEL,
    OPENROUTER_TO_NVIDIA_DIRECT_MODELS,
    NemotronClient,
    _map_models_to_nvidia_direct,
)
from src.network import NetworkError, _resolve
from tests.test_discovery import IMAGE, URI, OfflineDiscoveryTestCase, completion


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
    def test_text_fallback_order_is_router_then_nvidia_best_to_worst(self, transport, _sleep, _clock):
        # 404s walk the whole text fallback chain: default, free router,
        # then NVIDIA :free models best to worst (Ultra 550B, Nano Omni,
        # Lightning — ranked by the daily selector's scoring).
        transport.side_effect = [(404, b"")] * 4 + [completion('{"channels": ["proxychan"]}')]
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
            client.configure_cycle(max_attempts=10, deadline=30)
            self.assertEqual(client.complete_json("public query"), {"channels": ["proxychan"]})
        payloads = [json.loads(call.kwargs["data"]) for call in transport.call_args_list]
        self.assertEqual([p["model"] for p in payloads], [
            DEFAULT_OPENROUTER_MODEL,
            "openrouter/free",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            "nvidia/nemotron-3.5-lightning:free",
        ])
        # The default model gets reasoning + structured outputs; the router
        # gets no reasoning; the NVIDIA fallbacks keep reasoning (they
        # support it) but drop response_format (their free tier does not
        # advertise structured outputs).
        self.assertEqual(payloads[0]["reasoning"], {"effort": "high"})
        self.assertEqual(payloads[0]["response_format"], {"type": "json_object"})
        self.assertNotIn("reasoning", payloads[1])
        for p in payloads[2:]:
            self.assertEqual(p["reasoning"], {"effort": "high"})
            self.assertNotIn("response_format", p)

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_vision_fallback_order_is_router_then_nano_omni(self, transport, _sleep, _clock):
        # Only Nano Omni takes image input among NVIDIA :free models, so
        # the vision chain is default, free router, Nano Omni.
        transport.side_effect = [(200, IMAGE), (404, b""), (404, b""), completion(URI)]
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
            client.configure_cycle(max_attempts=10, deadline=30)
            self.assertIn("vless://", client.analyze_image("https://cdn.telegram.org/public.png"))
        image_payloads = [json.loads(call.kwargs["data"]) for call in transport.call_args_list[1:]]
        self.assertEqual([p["model"] for p in image_payloads], [
            DEFAULT_OPENROUTER_MODEL,
            "openrouter/free",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        ])

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

    def test_openrouter_to_nvidia_direct_mapping(self):
        # Pins the mapped IDs (verified 2026-11-15 against NVIDIA's own
        # docs + a live free-tier probe): a stale ID here costs a 404.
        self.assertEqual(OPENROUTER_TO_NVIDIA_DIRECT_MODELS, {
            "nvidia/nemotron-3.5-super-vl:free": "nvidia/nemotron-3.5-super-vl-120b-a12b",
            "nvidia/nemotron-3-ultra-550b-a55b:free": "nvidia/nemotron-3-ultra-550b-a55b",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
            "nvidia/nemotron-3.5-lightning:free": "nvidia/nemotron-3.5-lightning-30b-a3b",
            "stepfun-ai/step-5-preview": "stepfun-ai/step-5-preview",
        })
        # The free router has no direct twin and is skipped; order is
        # preserved, duplicates are dropped, unknown IDs are dropped.
        self.assertEqual(_map_models_to_nvidia_direct([
            DEFAULT_OPENROUTER_MODEL, "openrouter/free",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "unknown/model",
        ]), [
            "nvidia/nemotron-3.5-super-vl-120b-a12b",
            "nvidia/nemotron-3-ultra-550b-a55b",
        ])
        self.assertEqual(_map_models_to_nvidia_direct(["openrouter/free", "unknown/model"]), [])

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_openrouter_transport_failure_falls_back_to_nvidia_direct(self, transport, _sleep, _clock):
        # Run 43696446561 (2026-11-15): OpenRouter returned 503 BrokenPipe
        # for 43 minutes and the model-level fallbacks cannot help a dead
        # host. A transport failure now crosses to NVIDIA direct with the
        # model list mapped to NVIDIA-direct IDs (the :free IDs do not
        # exist there; the free router is skipped).
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
        # Transport failure must not burn every model on the dead host.
        self.assertLessEqual(sum("openrouter.ai" in u for u in urls), 3)
        nvidia_calls = [call for call in transport.call_args_list if "integrate.api.nvidia.com" in call.args[0]]
        self.assertTrue(nvidia_calls)
        payload = json.loads(nvidia_calls[0].kwargs["data"])
        self.assertEqual(payload["model"], "nvidia/nemotron-3.5-super-vl-120b-a12b")
        # The OpenRouter-shaped reasoning object is stripped on the
        # NVIDIA-direct call.
        self.assertNotIn("reasoning", payload)

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_openrouter_http_failure_on_all_models_falls_back_to_nvidia_direct(self, transport, _sleep, _clock):
        # HTTP 503 on the primary model, then on the free router, then on
        # every NVIDIA fallback model: the whole OpenRouter chain fails,
        # so the call crosses to NVIDIA direct with the mapped model list.
        transport.side_effect = [(503, b"")] * 15 + [completion('{"channels": ["proxychan"]}') ]
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-synthetic", "OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
            client.configure_cycle(max_attempts=20, deadline=30)
            self.assertEqual(client.complete_json("public query"), {"channels": ["proxychan"]})
        payloads = [json.loads(call.kwargs["data"]) for call in transport.call_args_list]
        self.assertEqual([p["model"] for p in payloads[:15]], [DEFAULT_OPENROUTER_MODEL] * 3 + ["openrouter/free"] * 3
                         + ["nvidia/nemotron-3-ultra-550b-a55b:free"] * 3
                         + ["nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"] * 3
                         + ["nvidia/nemotron-3.5-lightning:free"] * 3)
        urls = [call.args[0] for call in transport.call_args_list]
        self.assertTrue(all("openrouter.ai" in u for u in urls[:15]))
        self.assertTrue(all("integrate.api.nvidia.com" in u for u in urls[15:]))
        # The NVIDIA-direct call carries the mapped default model, with
        # the OpenRouter-shaped reasoning object and structured-output
        # request stripped (complete_json parses plain JSON text).
        nvidia_payload = payloads[15]
        self.assertEqual(nvidia_payload["model"], "nvidia/nemotron-3.5-super-vl-120b-a12b")
        self.assertNotIn("reasoning", nvidia_payload)
        self.assertNotIn("response_format", nvidia_payload)
        self.assertEqual(nvidia_payload["max_tokens"], 4096)

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_openrouter_failure_without_nvidia_key_does_not_fall_back(self, transport, _sleep, _clock):
        # No NVIDIA key configured: the cross-provider fallback cannot
        # run, so the OpenRouter failure stands and no NVIDIA call is made.
        transport.side_effect = [(503, b"")] * 15
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-synthetic", "NVIDIA_API_KEY": ""}):
            client = NemotronClient()
            client.configure_cycle(max_attempts=20, deadline=30)
            with self.assertRaises(RuntimeError):
                client.complete("public query")
        urls = [call.args[0] for call in transport.call_args_list]
        self.assertTrue(all("openrouter.ai" in u for u in urls))
        self.assertFalse(any("integrate.api.nvidia.com" in u for u in urls))

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_vision_fallback_to_nvidia_direct_uses_mapped_vision_model(self, transport, _sleep, _clock):
        # Vision chain fails on every OpenRouter model (503s): the image
        # call crosses to NVIDIA direct, where Super VL
        # (nvidia/nemotron-3.5-super-vl-120b-a12b) is a vision-language
        # model that takes image input. The image content is preserved.
        transport.side_effect = [(200, IMAGE)] + [(503, b"")] * 9 + [completion(URI)]
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-synthetic", "OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
            client.configure_cycle(max_attempts=20, deadline=30)
            self.assertIn("vless://", client.analyze_image("https://cdn.telegram.org/public.png"))
        calls = transport.call_args_list
        image_payloads = [json.loads(call.kwargs["data"]) for call in calls[1:]]
        self.assertEqual([p["model"] for p in image_payloads[:9]], [
            DEFAULT_OPENROUTER_MODEL,
        ] * 3 + ["openrouter/free"] * 3 + ["nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"] * 3)
        urls = [call.args[0] for call in calls[1:]]
        self.assertTrue(all("openrouter.ai" in u for u in urls[:9]))
        self.assertTrue(all("integrate.api.nvidia.com" in u for u in urls[9:]))
        nvidia_payload = image_payloads[9]
        self.assertEqual(nvidia_payload["model"], "nvidia/nemotron-3.5-super-vl-120b-a12b")
        user_content = nvidia_payload["messages"][1]["content"]
        self.assertTrue(any(part.get("type") == "image_url" for part in user_content))

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
        # Every attempt empty on all models: complete() returns "" (the
        # agent skips that prompt) instead of killing the whole run.
        transport.side_effect = [self._null_content()] * 15
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
            client.configure_cycle(max_attempts=20, deadline=30)
            self.assertEqual(client.complete("public query"), "")
        models = [json.loads(call.kwargs["data"])["model"] for call in transport.call_args_list]
        self.assertEqual(models, [DEFAULT_OPENROUTER_MODEL] * 3 + ["openrouter/free"] * 3
                         + ["nvidia/nemotron-3-ultra-550b-a55b:free"] * 3
                         + ["nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"] * 3
                         + ["nvidia/nemotron-3.5-lightning:free"] * 3)
        self.assertEqual(client.api_requests_count, 15)

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.time.sleep")
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_null_content_mixed_with_real_failure_still_raises(self, transport, _sleep, _clock):
        # Empty replies on the default model, then a real 503 on every
        # fallback model: that is a provider failure, not a skipped
        # prompt, so it raises.
        transport.side_effect = [self._null_content()] * 3 + [(503, b"")] * 12
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-synthetic"}):
            client = NemotronClient()
            client.configure_cycle(max_attempts=20, deadline=30)
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
