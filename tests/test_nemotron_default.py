import hashlib
import json
import os
import tempfile
from unittest.mock import patch

from src.discovery.nemotron_client import NemotronClient
from tests.test_discovery import IMAGE, URI, OfflineDiscoveryTestCase, completion


class TestNemotronDefault(OfflineDiscoveryTestCase):
    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_text_candidates_keep_default_or_override_first_without_duplicates(self, transport, _clock):
        cases = [
            ({}, None, [
                "stepfun-ai/step-5-preview",
                "moonshotai/kimi-k2.6",
                "mistralai/mistral-large-4-1t-a52b",
                "nvidia/nemotron-3-ultra-550b-a55b",
                "nvidia/nemotron-3.5-super-vl-120b-a12b",
            ]),
            ({"NVIDIA_MODEL": " moonshotai/kimi-k2.6 "}, None, [
                "moonshotai/kimi-k2.6",
                "mistralai/mistral-large-4-1t-a52b",
                "nvidia/nemotron-3-ultra-550b-a55b",
                "nvidia/nemotron-3.5-super-vl-120b-a12b",
            ]),
            ({"NVIDIA_MODEL": "moonshotai/kimi-k2.6"}, "mistralai/mistral-large-4-1t-a52b", [
                "mistralai/mistral-large-4-1t-a52b",
                "moonshotai/kimi-k2.6",
                "nvidia/nemotron-3-ultra-550b-a55b",
                "nvidia/nemotron-3.5-super-vl-120b-a12b",
            ]),
        ]
        for environment, model, expected in cases:
            with self.subTest(environment=environment, model=model), patch.dict(os.environ, environment):
                transport.reset_mock()
                transport.side_effect = [(404, b"")] * (len(expected) - 1) + [completion("public result")]
                client = NemotronClient(api_key="synthetic-key", model=model)
                client.configure_cycle(max_attempts=10, deadline=30)
                self.assertEqual(client.model, expected[0])
                self.assertEqual(client.complete("public query"), "public result")
                self.assertEqual(
                    [json.loads(call.kwargs["data"])["model"] for call in transport.call_args_list],
                    expected,
                )

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_step_default_text_and_image_payloads_carry_high_reasoning(self, transport, _clock):
        client = NemotronClient(api_key="synthetic-key")
        client.configure_cycle(max_attempts=10, deadline=30)
        transport.side_effect = [completion("public result")]
        self.assertEqual(client.complete("public query"), "public result")
        text_payload = json.loads(transport.call_args_list[0].kwargs["data"])
        self.assertEqual(text_payload["model"], "stepfun-ai/step-5-preview")
        self.assertEqual(text_payload["reasoning_effort"], "high")

        transport.reset_mock()
        transport.side_effect = [(200, IMAGE), completion(URI)]
        self.assertIn("vless://", client.analyze_image("https://cdn.telegram.org/public.png"))
        image_payload = json.loads(transport.call_args_list[1].kwargs["data"])
        self.assertEqual(image_payload["model"], "stepfun-ai/step-5-preview")
        self.assertEqual(image_payload["reasoning_effort"], "high")

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_fallback_models_do_not_carry_reasoning_effort(self, transport, _clock):
        client = NemotronClient(api_key="synthetic-key")
        client.configure_cycle(max_attempts=10, deadline=30)
        transport.side_effect = [(404, b""), completion("public result")]
        self.assertEqual(client.complete("public query"), "public result")
        text_payloads = [json.loads(call.kwargs["data"]) for call in transport.call_args_list]
        self.assertEqual([p["model"] for p in text_payloads],
                         ["stepfun-ai/step-5-preview", "moonshotai/kimi-k2.6"])
        self.assertEqual(text_payloads[0]["reasoning_effort"], "high")
        self.assertNotIn("reasoning_effort", text_payloads[1])

        transport.reset_mock()
        transport.side_effect = [(200, IMAGE), (404, b""), completion(URI)]
        self.assertIn("vless://", client.analyze_image("https://cdn.telegram.org/public.png"))
        image_payloads = [json.loads(call.kwargs["data"]) for call in transport.call_args_list[1:]]
        self.assertEqual([p["model"] for p in image_payloads],
                         ["stepfun-ai/step-5-preview", "moonshotai/kimi-k2.6"])
        self.assertEqual(image_payloads[0]["reasoning_effort"], "high")
        self.assertNotIn("reasoning_effort", image_payloads[1])

    @patch("src.discovery.nemotron_client.time.monotonic", return_value=0)
    @patch("src.discovery.nemotron_client.fetch_bytes")
    def test_explicit_non_step_override_does_not_carry_reasoning_effort(self, transport, _clock):
        client = NemotronClient(api_key="synthetic-key", model="moonshotai/kimi-k2.6")
        client.configure_cycle(max_attempts=10, deadline=30)
        transport.side_effect = [completion("public result")]
        self.assertEqual(client.complete("public query"), "public result")
        text_payload = json.loads(transport.call_args_list[0].kwargs["data"])
        self.assertEqual(text_payload["model"], "moonshotai/kimi-k2.6")
        self.assertNotIn("reasoning_effort", text_payload)

        transport.reset_mock()
        transport.side_effect = [(200, IMAGE), completion(URI)]
        self.assertIn("vless://", client.analyze_image("https://cdn.telegram.org/public.png"))
        image_payload = json.loads(transport.call_args_list[1].kwargs["data"])
        self.assertEqual(image_payload["model"], "moonshotai/kimi-k2.6")
        self.assertNotIn("reasoning_effort", image_payload)


class TestNemotronTrace(OfflineDiscoveryTestCase):
    @patch('src.discovery.nemotron_client.time.monotonic', return_value=0)
    def test_trace_file_is_local_jsonl_bounded_and_key_free(self, _clock):
        path = self.root / "ai_trace.jsonl"
        with patch.dict(os.environ, {"NAVAROUTE_AI_TRACE_PATH": str(path)}), \
                patch("src.discovery.nemotron_client.fetch_bytes", return_value=completion("public result")):
            client = NemotronClient(api_key="synthetic-key-do-not-leak")
            client.configure_cycle(max_attempts=5, deadline=30)
            self.assertEqual(client.complete("A" * 5000), "public result")
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["status"], 200)
        self.assertEqual(record["model"], "stepfun-ai/step-5-preview")
        expected_marker = "<truncated 5000 chars sha256=" + hashlib.sha256(b"A" * 5000).hexdigest() + ">"
        self.assertEqual(record["request"]["messages"][1]["content"], expected_marker)
        body = json.loads(record["response_text"])
        self.assertEqual(body["choices"][0]["message"]["content"], "public result")
        serialized = lines[0]
        for forbidden in ("synthetic-key-do-not-leak", "Authorization", "Bearer"):
            self.assertNotIn(forbidden, serialized)

    @patch('src.discovery.nemotron_client.time.monotonic', return_value=0)
    def test_env_unset_writes_nothing_and_call_is_transparent(self, _clock):
        with patch("src.discovery.nemotron_client.fetch_bytes", return_value=completion("public result")):
            client = NemotronClient(api_key="synthetic-key")
            client.configure_cycle(max_attempts=5, deadline=30)
            self.assertEqual(client.complete("public query"), "public result")
        self.assertFalse((self.root / "ai_trace.jsonl").exists())

    @patch('src.discovery.nemotron_client.time.monotonic', return_value=0)
    def test_trace_write_failure_never_breaks_the_api_path(self, _clock):
        missing_dir = self.root / "absent" / "ai_trace.jsonl"
        with patch.dict(os.environ, {"NAVAROUTE_AI_TRACE_PATH": str(missing_dir)}), \
                patch("src.discovery.nemotron_client.fetch_bytes", return_value=completion("public result")):
            client = NemotronClient(api_key="synthetic-key")
            client.configure_cycle(max_attempts=5, deadline=30)
            self.assertEqual(client.complete("public query"), "public result")
