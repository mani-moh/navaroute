import json
import os
from unittest.mock import patch

from src.discovery.nemotron_client import NemotronClient
from tests.test_discovery import OfflineDiscoveryTestCase, completion


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
