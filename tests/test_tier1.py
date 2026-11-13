"""Tests for the tier-1 / tier-2 PattN testing system (src/tier1, src/pattn_client).

The WebAPI itself is not run here (no Windows host in CI); the client
is tested against a mocked HTTP layer, and the tier-1 logic is tested
purely. The first real WebAPI run is the tier-1 workflow on a Windows
runner.
"""
from __future__ import annotations

import base64
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.pattn_client import PattnClient, PattnError
from src.tier1 import (TIER_GROUPS, backoff_hours, build_results,
                       build_variants, load_blocked, load_state,
                       passing_fingerprints, render_feed, save_blocked,
                       save_state, select_due, update_state)

NOW = datetime(2026, 11, 13, 22, 0, tzinfo=timezone.utc)


def _entry(uri: str) -> dict[str, str]:
    return {"uri": uri, "fingerprint": "fp-" + uri.split("://")[1].split("@")[0]}


class BackoffTests(unittest.TestCase):
    def test_intervals_grow_with_the_streak_and_cap(self):
        self.assertEqual([backoff_hours(s) for s in range(7)], [0, 4, 8, 16, 32, 48, 48])

    def test_never_tested_is_due_first_then_lowest_streak_then_oldest(self):
        known = [_entry("vless://a@x.example:443"), _entry("vless://b@x.example:443"),
                 _entry("vless://c@x.example:443"), _entry("vless://d@x.example:443")]
        state = {
            "fp-b": {"failures": 2, "last_test": "2026-11-13T10:00:00+00:00", "last_delay": None},
            "fp-c": {"failures": 1, "last_test": "2026-11-13T10:00:00+00:00", "last_delay": None},
            "fp-d": {"failures": 1, "last_test": "2026-11-13T21:00:00+00:00", "last_delay": None},
        }
        due = select_due(known, state, NOW, 10)
        # a: never tested. c: streak 1, tested 12h ago (due, interval 4h).
        # d: streak 1, tested 1h ago (not due). b: streak 2, interval 8h,
        # tested 12h ago (due) -- after c because c is older within streak 1.
        self.assertEqual([e["fingerprint"] for e in due], ["fp-a", "fp-c", "fp-b"])

    def test_cap_is_respected(self):
        known = [_entry(f"vless://n{i}@x.example:443") for i in range(5)]
        self.assertEqual(len(select_due(known, {}, NOW, 2)), 2)


class StateTests(unittest.TestCase):
    def test_pass_resets_the_streak_and_failure_grows_it(self):
        state: dict[str, dict] = {}
        state = update_state(state, {"fp-a": 123, "fp-b": None}, NOW)
        self.assertEqual(state["fp-a"], {"failures": 0, "last_test": NOW.isoformat(), "last_delay": 123})
        self.assertEqual(state["fp-b"], {"failures": 1, "last_test": NOW.isoformat(), "last_delay": None})
        state = update_state(state, {"fp-b": None}, NOW + timedelta(hours=5))
        self.assertEqual(state["fp-b"]["failures"], 2)
        state = update_state(state, {"fp-b": 45}, NOW + timedelta(hours=14))
        self.assertEqual(state["fp-b"], {"failures": 0, "last_test": (NOW + timedelta(hours=14)).isoformat(), "last_delay": 45})

    def test_passing_set_is_last_test_passed_only(self):
        state = {"fp-a": {"failures": 0, "last_test": NOW.isoformat(), "last_delay": 10},
                 "fp-b": {"failures": 1, "last_test": NOW.isoformat(), "last_delay": None},
                 "fp-c": {"failures": 0, "last_test": NOW.isoformat(), "last_delay": None}}
        self.assertEqual(passing_fingerprints(state), {"fp-a"})

    def test_state_round_trips_and_rejects_bad_files(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tier1_state.json"
            entries = {"fp-a": {"failures": 0, "last_test": NOW.isoformat(), "last_delay": 12}}
            save_state(path, entries)
            self.assertEqual(load_state(path), entries)
            path.write_text(json.dumps({"version": 99, "entries": {}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_state(path)
            path.write_text(json.dumps({"version": 1, "entries": {"fp-a": {"failures": -1, "last_test": NOW.isoformat(), "last_delay": None}}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_state(path)

    def test_blocked_round_trips_and_rejects_bad_files(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "github_blocked.json"
            entries = {"fp-a": {"iran_delay": 300, "added": NOW.isoformat(), "last_iran_pass": NOW.isoformat()}}
            save_blocked(path, entries)
            self.assertEqual(load_blocked(path), entries)
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_blocked(path)

    def test_missing_files_are_empty_and_every_validation_branch_rejects(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertEqual(load_state(base / "nope.json"), {})
            self.assertEqual(load_blocked(base / "nope.json"), {})
            state_path = base / "state.json"
            blocked_path = base / "blocked.json"
            good_entry = {"failures": 0, "last_test": NOW.isoformat(), "last_delay": 12}
            for payload in ("not json", {"version": 1, "entries": {"fp-a": "nope"}},
                            {"version": 1, "entries": {"fp-a": {**good_entry, "failures": -1}}},
                            {"version": 1, "entries": {"fp-a": {**good_entry, "last_delay": -5}}},
                            {"version": 1, "entries": {"fp-a": {**good_entry, "last_test": "2026-11-13T22:00:00+02:00"}}},
                            {"version": 1, "entries": {"fp-a": {**good_entry, "last_test": "not-a-time"}}}):
                state_path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    load_state(state_path)
            good_blocked = {"iran_delay": 300, "added": NOW.isoformat(), "last_iran_pass": NOW.isoformat()}
            for payload in ({"version": 1, "entries": {"fp-a": {**good_blocked, "iran_delay": "fast"}}},
                            {"version": 1, "entries": {"fp-a": {**good_blocked, "added": "2026-11-13T22:00:00+02:00"}}},
                            {"version": 2, "entries": {}}):
                blocked_path.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    load_blocked(blocked_path)
            with patch("src.tier1.STATE_MAX_BYTES", 10):
                state_path.write_text(json.dumps({"version": 1, "entries": {}}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_state(state_path)
                with self.assertRaises(ValueError):
                    load_blocked(state_path)


class VariantTests(unittest.TestCase):
    def setUp(self):
        self.feed_uris = {
            "all": ["vless://a@x.example:443", "vless://b@x.example:443",
                    "vless://c@x.example:443", "vless://d@x.example:443"],
            "reachable": ["vless://a@x.example:443", "vless://b@x.example:443"],
        }
        self.uri_to_fp = {uri: "fp-" + uri.split("://")[1].split("@")[0]
                          for uris in self.feed_uris.values() for uri in uris}
        self.state = {
            "fp-a": {"failures": 0, "last_test": NOW.isoformat(), "last_delay": 200},
            "fp-b": {"failures": 3, "last_test": NOW.isoformat(), "last_delay": None},
            "fp-c": {"failures": 0, "last_test": NOW.isoformat(), "last_delay": 50},
        }
        self.blocked = {"fp-b": {"iran_delay": 400, "added": NOW.isoformat(),
                                 "last_iran_pass": NOW.isoformat()}}

    def test_variants_keep_passing_plus_blocked_sorted_by_delay(self):
        variants = build_variants(self.feed_uris, self.uri_to_fp, self.state, self.blocked)
        # all: c (50), a (200), b (blocked, Iran 400). d never tested: out.
        self.assertEqual(variants["all"],
                         ["vless://c@x.example:443", "vless://a@x.example:443",
                          "vless://b@x.example:443"])
        self.assertEqual(variants["reachable"],
                         ["vless://a@x.example:443", "vless://b@x.example:443"])

    def test_results_count_tested_passed_and_blocked_only(self):
        tested = {"fp-a", "fp-b", "fp-c"}
        passing = passing_fingerprints(self.state)
        report = build_results(self.feed_uris, self.uri_to_fp, tested, passing,
                               self.blocked, NOW)
        self.assertEqual(report["feeds"]["all"], {"tested": 3, "passed": 2, "github_blocked": 1})
        self.assertEqual(report["feeds"]["reachable"], {"tested": 2, "passed": 1, "github_blocked": 1})
        # A blocked config that also passes tier 1 is not double-counted.
        state2 = dict(self.state)
        state2["fp-b"] = {"failures": 0, "last_test": NOW.isoformat(), "last_delay": 90}
        report2 = build_results(self.feed_uris, self.uri_to_fp, tested,
                                passing_fingerprints(state2), self.blocked, NOW)
        self.assertEqual(report2["feeds"]["all"]["github_blocked"], 0)

    def test_render_feed_matches_the_publisher_format(self):
        plain, encoded = render_feed(["vless://a@x.example:443"], "Tier 1 — All", NOW.isoformat(), 2)
        self.assertTrue(plain.startswith("#profile-title: base64:"))
        self.assertIn("#profile-update-interval: 2\n", plain)
        self.assertTrue(plain.endswith("vless://a@x.example:443\n"))
        self.assertEqual(base64.b64decode(encoded).decode(), "vless://a@x.example:443")
        self.assertEqual(TIER_GROUPS, ("all", "reachable", "active", "top100", "lite500"))


class ClientTests(unittest.TestCase):
    def _client(self, responses):
        client = PattnClient(api_key="test-key")
        calls = []

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def read(self):
                return json.dumps(self.payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=None):
            calls.append((request.method, request.full_url,
                          json.loads(request.data) if request.data else None))
            method, url, _ = calls[-1]
            key = (method, url.split("/", 3)[-1])
            if key not in responses:
                raise AssertionError(f"unexpected call {key}")
            return FakeResponse(responses[key])

        return client, calls, fake_urlopen

    def test_login_import_speedtest_and_profiles(self):
        responses = {
            ("POST", "api/auth/login"): {"data": {"token": "tok-123"}},
            ("POST", "api/profiles/import"): {"data": {"detail": "imported"}},
            ("POST", "api/speedtests"): {"data": {}},
            ("GET", "api/profiles"): {"data": [
                {"IndexId": "1", "Protocol": "vless", "Address": "x.example",
                 "Port": 443, "Delay": 120, "CanTest": True}]},
            ("GET", "api/status"): {"data": {"operations": []}},
        }
        client, calls, fake_urlopen = self._client(responses)
        with patch("urllib.request.urlopen", fake_urlopen):
            self.assertEqual(client.login(), "tok-123")
            self.assertEqual(client.import_profiles(["vless://a@x.example:443"]), 1)
            client.start_speedtest()
            profiles = client.profiles()
        self.assertEqual(profiles[0]["Delay"], 120)
        self.assertEqual(calls[0][2], {"Key": "test-key"})
        self.assertEqual(calls[1][2]["Content"], "vless://a@x.example:443")
        self.assertEqual(calls[2][2], {"Action": "Realping"})

    def test_login_accepts_a_bare_string_token(self):
        responses = {("POST", "api/auth/login"): {"data": "tok-abc"}}
        client, _, fake_urlopen = self._client(responses)
        with patch("urllib.request.urlopen", fake_urlopen):
            self.assertEqual(client.login(), "tok-abc")

    def test_http_error_becomes_a_pattn_error(self):
        import io
        import urllib.error
        client = PattnClient(api_key="k")

        def failing(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {},
                                         io.BytesIO(b"forbidden"))

        with patch("urllib.request.urlopen", failing):
            with self.assertRaises(PattnError):
                client.login()


class ClientLaunchTests(unittest.TestCase):
    def test_pinned_webapi_urls_use_the_real_asset_names(self):
        from src.pattn_client import PINNED_WEBAPI_LINUX_URL, PINNED_WEBAPI_URL
        self.assertTrue(PINNED_WEBAPI_URL.endswith("7.25.6-P40/PattN-windows-64-WebAPI.zip"))
        self.assertTrue(PINNED_WEBAPI_LINUX_URL.endswith("7.25.6-P40/PattN-linux-64-WebAPI.zip"))

    def test_generate_api_key_is_random_and_nonempty(self):
        from src.pattn_client import generate_api_key
        first, second = generate_api_key(), generate_api_key()
        self.assertTrue(first and second and first != second)

    def test_health_true_and_false(self):
        client = PattnClient(api_key="k")
        with patch.object(client, "_request", return_value={"ok": True}):
            self.assertTrue(client.health())
        with patch.object(client, "_request", side_effect=PattnError("down")):
            self.assertFalse(client.health())

    def test_request_transport_edges(self):
        import urllib.error
        client = PattnClient(api_key="k")

        class FakeResponse:
            def __init__(self, raw):
                self.raw = raw

            def read(self):
                return self.raw

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("urllib.request.urlopen", return_value=FakeResponse(b"")):
            self.assertIsNone(client._request("GET", "/api/health", auth=False))
        with patch("urllib.request.urlopen", return_value=FakeResponse(b"plain text")):
            self.assertEqual(client._request("GET", "/api/health", auth=False), "plain text")
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("no route")):
            with self.assertRaises(PattnError):
                client._request("GET", "/api/health", auth=False)

    def test_login_requires_a_key_and_a_token(self):
        with self.assertRaises(PattnError):
            PattnClient(api_key="").login()
        client = PattnClient(api_key="k")
        with patch.object(client, "_request", return_value={"data": {}}):
            with self.assertRaises(PattnError):
                client.login()

    def test_profiles_rejects_a_non_list_and_delete_skips_empty(self):
        client = PattnClient(api_key="k")
        with patch.object(client, "_request", return_value={"data": {"not": "a list"}}):
            with self.assertRaises(PattnError):
                client.profiles()
        with patch.object(client, "_request") as request:
            client.delete_profiles([])
            request.assert_not_called()

    def test_import_chunks_and_iran_preset_and_stop_swallows_errors(self):
        client = PattnClient(api_key="k")
        calls = []
        with patch.object(client, "_request", side_effect=lambda m, p, b=None, **kw: calls.append((m, p, b))), \
                patch("src.pattn_client.IMPORT_CHUNK", 2):
            self.assertEqual(client.import_profiles(["u1", "u2", "u3", "u4", "u5"]), 5)
        self.assertEqual(len([c for c in calls if c[1] == "/api/profiles/import"]), 3)
        self.assertEqual(calls[-1][2]["Content"], "u5")
        with patch.object(client, "_request") as request:
            client.apply_iran_preset()
            request.assert_called_once_with("POST", "/api/settings/regional-presets/Iran", {})
        with patch.object(client, "_request", side_effect=PattnError("gone")):
            client.stop_speedtest()  # swallows the error; a stopped test is fine

    def test_speedtest_running_reads_status(self):
        client = PattnClient(api_key="k")
        with patch.object(client, "status", return_value={"operations": [{"kind": "speedtest", "state": "running"}]}):
            self.assertTrue(client.speedtest_running())
        with patch.object(client, "status", return_value={"operations": []}):
            self.assertFalse(client.speedtest_running())
        with patch.object(client, "status", side_effect=PattnError("down")):
            self.assertFalse(client.speedtest_running())

    def test_wait_for_speedtest_returns_and_times_out(self):
        client = PattnClient(api_key="k")
        with patch.object(client, "speedtest_running", return_value=False), \
                patch("src.pattn_client.time.sleep"):
            client.wait_for_speedtest(timeout=10)
        with patch.object(client, "speedtest_running", return_value=True), \
                patch("src.pattn_client.time.monotonic", side_effect=[0.0, 0.0, 100.0]), \
                patch("src.pattn_client.time.sleep"):
            with self.assertRaises(PattnError):
                client.wait_for_speedtest(timeout=10)

    def test_start_and_stop_webapi(self):
        from src import pattn_client
        from unittest.mock import MagicMock
        process = MagicMock()
        process.poll.return_value = None
        with patch.object(pattn_client.subprocess, "Popen", return_value=process) as popen, \
                patch.object(PattnClient, "health", return_value=True):
            started = pattn_client.start_webapi(Path("/tmp/webapi/v2rayN.WebAPI.exe"), "k")
        self.assertIs(started, process)
        self.assertIn("V2RAYN_WEB_API_KEY", popen.call_args.kwargs["env"])
        process.poll.return_value = 1
        with patch.object(pattn_client.subprocess, "Popen", return_value=process), \
                patch.object(PattnClient, "health", return_value=False):
            with self.assertRaises(PattnError):
                pattn_client.start_webapi(Path("/tmp/webapi/v2rayN.WebAPI.exe"), "k")
        process.poll.return_value = None
        with patch.object(pattn_client.subprocess, "Popen", return_value=process), \
                patch.object(PattnClient, "health", return_value=False), \
                patch.object(pattn_client.time, "monotonic", side_effect=[0.0, 0.0, 200.0]), \
                patch.object(pattn_client.time, "sleep"):
            with self.assertRaises(PattnError):
                pattn_client.start_webapi(Path("/tmp/webapi/v2rayN.WebAPI.exe"), "k")
            process.terminate.assert_called()
        import subprocess as _subprocess
        done = MagicMock()
        done.poll.return_value = 0
        pattn_client.stop_webapi(done)
        done.terminate.assert_not_called()
        running = MagicMock()
        running.poll.return_value = None
        running.wait.return_value = None
        pattn_client.stop_webapi(running)
        running.terminate.assert_called_once()
        stuck = MagicMock()
        stuck.poll.return_value = None
        stuck.wait.side_effect = _subprocess.TimeoutExpired("p", 15)
        pattn_client.stop_webapi(stuck)
        stuck.kill.assert_called_once()


class PrivacyGateTests(unittest.TestCase):
    def test_tier_files_are_public_outputs(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import public_git
        for path in ("data/tier1_state.json", "data/tier1_results.json",
                     "data/github_blocked.json", "data/iran_results.json",
                     "subs/tier1_all.txt", "subs/tier1_all_base64.txt",
                     "subs/tier1_top100.txt", "subs/iran_reachable.txt",
                     "subs/iran_lite500_base64.txt"):
            self.assertTrue(public_git.public_output(path), path)
        for path in ("subs/tier1_verified.txt", "subs/iran_verified.txt",
                     "subs/tier1_all.json", "data/tier1_state.json.bak"):
            self.assertFalse(public_git.public_output(path), path)


if __name__ == "__main__":
    unittest.main()
