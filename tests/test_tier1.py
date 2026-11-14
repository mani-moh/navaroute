"""Tests for the tier-1 / tier-2 PattN testing system (src/tier1, src/pattn_client).

The WebAPI itself is not run here (no Windows host in CI); the client
is tested against a mocked HTTP layer, and the tier-1 logic is tested
purely. The first real WebAPI run is the tier-1 workflow on a Windows
runner.
"""
from __future__ import annotations

import base64
import io
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

    def test_logs_returns_entries_and_tolerates_errors(self):
        client = PattnClient(api_key="k")
        with patch.object(client, "_request", return_value=[
                {"Source": "SpeedtestService", "Message": "boom"}, "junk"]):
            logs = client.logs(limit=5, filter_text="speedtest")
        self.assertEqual(logs, [{"Source": "SpeedtestService", "Message": "boom"}])
        with patch.object(client, "_request", side_effect=PattnError("down")):
            self.assertEqual(client.logs(), [])

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

    def test_speedtest_running_reads_operations_not_status(self):
        # GET /api/operations returns a plain list of names; "speedtest"
        # is present while a test runs. /api/status carries no operations
        # field, so reading it always said False (run 43491830408).
        client = PattnClient(api_key="k")
        with patch.object(client, "_request", return_value=["speedtest", "core"]):
            self.assertTrue(client.speedtest_running())
            self.assertEqual(client.operations(), ["speedtest", "core"])
        with patch.object(client, "_request", return_value=["core"]):
            self.assertFalse(client.speedtest_running())
        with patch.object(client, "_request", return_value="not a list"):
            self.assertEqual(client.operations(), [])
        with patch.object(client, "_request", side_effect=PattnError("down")):
            self.assertFalse(client.speedtest_running())

    def test_envelope_normalizes_camelcase_keys(self):
        # The WebAPI serializes camelCase; the client reads PascalCase.
        # Without this, every profile's Delay read as None and tier 1
        # concluded 200 profiles were untested (run 43495662919).
        from src.pattn_client import _envelope
        self.assertEqual(
            _envelope({"data": [{"indexId": "1", "delay": -1, "coreType": "xray"}]}),
            [{"IndexId": "1", "Delay": -1, "CoreType": "xray"}])
        self.assertEqual(_envelope({"data": {"token": "abc"}}), {"Token": "abc"})
        self.assertEqual(_envelope({"data": ["speedtest"]}), ["speedtest"])
        self.assertEqual(_envelope({"data": "tok"}), "tok")

    def test_login_reads_normalized_token_key(self):
        client = PattnClient(api_key="k")
        with patch.object(client, "_request", return_value={"Token": "tok123"}):
            self.assertEqual(client.login(), "tok123")
        with patch.object(client, "_request", return_value="tok456"):
            self.assertEqual(client.login(), "tok456")

    def test_envelope_refusal_raises(self):
        from src.pattn_client import _envelope
        with self.assertRaises(PattnError):
            _envelope({"success": False, "code": "speedtest_busy"})
        self.assertEqual(_envelope({"success": True, "data": [1]}), [1])
        self.assertEqual(_envelope({"data": {"ok": True}}), {"Ok": True})

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
        self.assertIn("--foreground", popen.call_args.args[0])
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


class SpeedtestConfigTests(unittest.TestCase):
    def _load_client_module(self):
        from src import pattn_client
        return pattn_client

    def test_write_config_creates_file_and_sets_page_size(self):
        mod = self._load_client_module()
        with TemporaryDirectory() as tmp:
            exe = Path(tmp) / "v2rayN.WebAPI.exe"
            exe.touch()
            path = mod.write_webapi_speedtest_config(exe, 100)
            self.assertEqual(path, Path(tmp) / "guiConfigs" / "guiNConfig.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["SpeedTestItem"]["SpeedTestPageSize"], 100)

    def test_write_config_merges_with_existing_settings(self):
        mod = self._load_client_module()
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "guiConfigs"
            config_dir.mkdir()
            existing = {"SpeedTestItem": {"SpeedTestTimeout": 10,
                                          "SpeedTestPageSize": 16},
                        "CoreBasicItem": {"Loglevel": "warning"}}
            (config_dir / "guiNConfig.json").write_text(json.dumps(existing))
            exe = Path(tmp) / "v2rayN.WebAPI.exe"
            exe.touch()
            mod.write_webapi_speedtest_config(exe, 100)
            data = json.loads((config_dir / "guiNConfig.json").read_text())
            self.assertEqual(data["SpeedTestItem"]["SpeedTestPageSize"], 100)
            self.assertEqual(data["SpeedTestItem"]["SpeedTestTimeout"], 10)
            self.assertEqual(data["CoreBasicItem"]["Loglevel"], "warning")

    def test_write_config_rejects_bad_page_size_and_bad_files(self):
        mod = self._load_client_module()
        with TemporaryDirectory() as tmp:
            exe = Path(tmp) / "v2rayN.WebAPI.exe"
            exe.touch()
            with self.assertRaises(PattnError):
                mod.write_webapi_speedtest_config(exe, 0)
            config_dir = Path(tmp) / "guiConfigs"
            config_dir.mkdir()
            (config_dir / "guiNConfig.json").write_text("{not json")
            with self.assertRaises(PattnError):
                mod.write_webapi_speedtest_config(exe, 100)
            (config_dir / "guiNConfig.json").write_text("[1, 2]")
            with self.assertRaises(PattnError):
                mod.write_webapi_speedtest_config(exe, 100)
            (config_dir / "guiNConfig.json").write_text(
                json.dumps({"SpeedTestItem": "nope"}))
            path = mod.write_webapi_speedtest_config(exe, 50)
            self.assertEqual(json.loads(path.read_text())["SpeedTestItem"],
                             {"SpeedTestPageSize": 50})

    def test_prepare_worker_dir_reuses_base_for_worker_zero(self):
        mod = self._load_client_module()
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "webapi"
            base.mkdir()
            exe = base / "v2rayN.WebAPI.exe"
            exe.write_text("exe")
            (base / "guiConfigs").mkdir()
            (base / "guiConfigs" / "guiNConfig.json").write_text("{}")
            (base / "v2rayN.WebAPI.instance.lock").write_text("")
            self.assertEqual(mod.prepare_webapi_worker_dir(base, exe, 0), exe)
            self.assertFalse((base / "guiConfigs").exists())
            self.assertFalse((base / "v2rayN.WebAPI.instance.lock").exists())
            self.assertTrue(exe.exists())

    def test_prepare_worker_dir_copies_for_other_workers(self):
        mod = self._load_client_module()
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "webapi"
            base.mkdir()
            exe = base / "v2rayN.WebAPI.exe"
            exe.write_text("exe")
            (base / "guiConfigs").mkdir()
            (base / "guiConfigs" / "guiNDB.db").write_text("db")
            worker_exe = mod.prepare_webapi_worker_dir(base, exe, 2)
            self.assertEqual(worker_exe, Path(tmp) / "webapi.worker-2" / "v2rayN.WebAPI.exe")
            self.assertEqual(worker_exe.read_text(), "exe")
            self.assertFalse((Path(tmp) / "webapi.worker-2" / "guiConfigs").exists())
            self.assertTrue((base / "guiConfigs" / "guiNDB.db").exists())
            # Re-preparing the same worker replaces the stale copy.
            (Path(tmp) / "webapi.worker-2" / "stale.txt").write_text("stale")
            mod.prepare_webapi_worker_dir(base, exe, 2)
            self.assertFalse((Path(tmp) / "webapi.worker-2" / "stale.txt").exists())

    def test_prepare_worker_dir_removes_stale_state_files(self):
        mod = self._load_client_module()
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "webapi"
            base.mkdir()
            exe = base / "v2rayN.WebAPI.exe"
            exe.write_text("exe")
            (base / "guiLogs").write_text("stale log file, not a directory")
            mod.prepare_webapi_worker_dir(base, exe, 0)
            self.assertFalse((base / "guiLogs").exists())
            self.assertTrue(exe.exists())

    def test_start_webapi_sets_the_port_explicitly(self):
        from src import pattn_client
        from unittest.mock import MagicMock
        process = MagicMock()
        process.poll.return_value = None
        with patch.object(pattn_client.subprocess, "Popen", return_value=process) as popen, \
                patch.object(PattnClient, "health", return_value=True):
            pattn_client.start_webapi(Path("/tmp/webapi/v2rayN.WebAPI.exe"), "k",
                                      port=5081)
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["ASPNETCORE_URLS"], "http://127.0.0.1:5081")
        self.assertEqual(env["V2RAYN_WEB_API_KEY"], "k")


class _FakeWorkerClient:
    """A PattnClient stand-in for the feeder: import appends Delay-0
    profiles, and start_speedtest resolves them all to Delay -1 at once
    (or never, when resolve=False). profiles() raises PattnError from
    the ``fail_on_call``-th call onwards."""

    def __init__(self, resolve=True, running=False, fail_on_call=None):
        self._profiles: list[dict] = []
        self._next = 0
        self.resolve = resolve
        self.running = running
        self.fail_on_call = fail_on_call
        self.calls = 0
        self.started = 0

    def profiles(self):
        self.calls += 1
        if self.fail_on_call is not None and self.calls >= self.fail_on_call:
            raise PattnError("server died")
        return [dict(p) for p in self._profiles]

    def login(self):
        return "fake-token"

    def import_profiles(self, uris):
        for _ in uris:
            self._next += 1
            self._profiles.append({"IndexId": str(self._next), "Protocol": "vless",
                                  "Address": "10.0.0.%d" % self._next, "Port": 443,
                                  "Delay": 0, "CanTest": True, "CoreType": "xray"})
        return len(uris)

    def start_speedtest(self, profile_ids=None, action="Realping"):
        self.started += 1
        if self.resolve:
            for p in self._profiles:
                if p["Delay"] == 0:
                    p["Delay"] = -1

    def operations(self):
        return []

    def speedtest_running(self):
        return self.running

    def logs(self, limit=50, filter_text=None):
        return []


class FeederTests(unittest.TestCase):
    def _load_driver(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import tier1_pattn
        return tier1_pattn

    def _worker(self, driver, index=0, client=None):
        from unittest.mock import MagicMock
        return driver.Tier1Worker(index, Path(f"/tmp/webapi-{index}/v2rayN.WebAPI.exe"),
                                  5080 + index, MagicMock(),
                                  client or _FakeWorkerClient())

    def test_chunk_list_splits_and_rejects_bad_size(self):
        driver = self._load_driver()
        self.assertEqual(driver.chunk_list(["a", "b", "c", "d", "e"], 2),
                         [["a", "b"], ["c", "d"], ["e"]])
        self.assertEqual(driver.chunk_list([], 100), [])
        self.assertEqual(driver.chunk_list(["a"], 100), [["a"]])
        with self.assertRaises(ValueError):
            driver.chunk_list(["a"], 0)

    def test_assign_chunk_tracks_only_the_new_profiles(self):
        driver = self._load_driver()
        worker = self._worker(driver)
        driver._assign_chunk(worker, ["vless://a@10.0.0.1:443"], 1)
        self.assertEqual(worker.chunk_ids, ["1"])
        self.assertEqual(worker.chunk_attempts, 1)
        driver._assign_chunk(worker, ["vless://b@10.0.0.2:443"], 1)
        self.assertEqual(worker.chunk_ids, ["2"])

    def test_chunk_done_when_all_delayed_or_stalled(self):
        driver = self._load_driver()
        worker = self._worker(driver)
        driver._assign_chunk(worker, ["vless://a@10.0.0.1:443"], 1)
        done, untested = driver._chunk_done(worker)
        self.assertTrue(done)
        self.assertEqual(untested, 0)
        stalled = self._worker(driver, client=_FakeWorkerClient(resolve=False))
        driver._assign_chunk(stalled, ["vless://a@10.0.0.1:443"], 1)
        for _ in range(3):
            done, untested = driver._chunk_done(stalled)
            self.assertFalse(done)
        done, untested = driver._chunk_done(stalled)
        self.assertTrue(done)
        self.assertEqual(untested, 1)

    def test_feeder_refills_workers_until_all_chunks_done(self):
        driver = self._load_driver()
        workers = [self._worker(driver, index) for index in range(2)]
        chunks = [["u%d" % i] * 3 for i in range(5)]
        profiles = driver.run_speedtests(workers, chunks, timeout=60, poll=0)
        self.assertEqual(len(profiles), 15)
        self.assertEqual(sorted(p["Delay"] for p in profiles), [-1] * 15)
        self.assertTrue(all(w.tested_chunks > 0 for w in workers))
        self.assertEqual(sum(w.tested_chunks for w in workers), 5)

    def test_feeder_requeues_a_chunk_when_its_worker_dies(self):
        driver = self._load_driver()
        dying = self._worker(driver, 0, _FakeWorkerClient(fail_on_call=3))
        healthy = self._worker(driver, 1)
        chunks = [["u%d" % i] * 2 for i in range(2)]
        profiles = driver.run_speedtests([dying, healthy], chunks,
                                         timeout=60, poll=0)
        self.assertEqual(len(profiles), 4)
        self.assertFalse(dying.alive)
        self.assertTrue(healthy.alive)

    def test_collect_chunk_falls_back_to_all_profiles(self):
        driver = self._load_driver()
        worker = self._worker(driver)
        worker.client.import_profiles(["u1", "u2"])
        worker.chunk = ["u1", "u2"]
        worker.chunk_ids = ["99"]  # no profile carries this id
        collected = driver._collect_chunk(worker)
        self.assertEqual(len(collected), 2)
        self.assertIsNone(worker.chunk)

    def test_feeder_drops_a_chunk_after_two_attempts(self):
        driver = self._load_driver()
        first = self._worker(driver, 0, _FakeWorkerClient(fail_on_call=1))
        second = self._worker(driver, 1, _FakeWorkerClient(fail_on_call=1))
        profiles = driver.run_speedtests([first, second], [["u1", "u2"]],
                                         timeout=60, poll=0)
        self.assertEqual(profiles, [])
        self.assertFalse(first.alive)
        self.assertFalse(second.alive)

    def test_feeder_tolerates_a_stop_failure_when_a_worker_dies(self):
        driver = self._load_driver()
        dying = self._worker(driver, 0, _FakeWorkerClient(fail_on_call=3))
        healthy = self._worker(driver, 1)
        chunks = [["u%d" % i] * 2 for i in range(2)]
        with patch.object(driver, "stop_webapi", side_effect=RuntimeError("boom")):
            profiles = driver.run_speedtests([dying, healthy], chunks,
                                             timeout=60, poll=0)
        self.assertEqual(len(profiles), 4)

    def test_feeder_tolerates_a_collect_failure_on_timeout(self):
        driver = self._load_driver()
        slow = self._worker(driver, 0, _FakeWorkerClient(
            resolve=False, running=True, fail_on_call=3))
        profiles = driver.run_speedtests([slow], [["u1", "u2"]],
                                         timeout=0, poll=0)
        self.assertEqual(profiles, [])

    def test_feeder_returns_partial_results_on_timeout(self):
        driver = self._load_driver()
        slow = self._worker(driver, 0,
                            _FakeWorkerClient(resolve=False, running=True))
        profiles = driver.run_speedtests([slow], [["u1", "u2"]],
                                         timeout=0, poll=0)
        self.assertEqual(len(profiles), 2)
        self.assertTrue(all(p["Delay"] == 0 for p in profiles))

    def test_feeder_with_no_workers_returns_nothing(self):
        driver = self._load_driver()
        self.assertEqual(driver.run_speedtests([], [["u1"]], timeout=60), [])

    def test_prepare_workers_skips_a_failed_start_and_raises_when_none(self):
        driver = self._load_driver()
        from unittest.mock import MagicMock
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "webapi"
            base.mkdir()
            exe = base / "v2rayN.WebAPI.exe"
            exe.write_text("exe")
            process = MagicMock()
            client = MagicMock()
            calls = {"n": 0}

            def flaky_start(worker_exe, api_key, port):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise PattnError("bind failed")
                return process

            with patch.object(driver, "start_webapi", side_effect=flaky_start), \
                    patch.object(driver, "PattnClient", return_value=client):
                workers = driver.prepare_workers(base, exe, 3, 100, "key")
            self.assertEqual([w.index for w in workers], [0, 2])
            self.assertEqual([w.port for w in workers], [5080, 5082])
            with patch.object(driver, "start_webapi",
                              side_effect=PattnError("down")), \
                    patch.object(driver, "PattnClient", return_value=client):
                with self.assertRaises(PattnError):
                    driver.prepare_workers(base, exe, 2, 100, "key")


class DriverFeedTests(unittest.TestCase):
    def _load_driver(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import tier1_pattn
        return tier1_pattn

    def test_untested_run_prints_webapi_logs(self):
        import io
        from unittest.mock import MagicMock
        driver = self._load_driver()
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "all.txt").write_text(
                "vless://11111111-1111-1111-1111-111111111111@1.2.3.4:443#test\n",
                encoding="utf-8")
            client = MagicMock()
            client.profiles.return_value = [
                {"IndexId": "1", "Protocol": "vless", "Address": "1.2.3.4",
                 "Port": 443, "Delay": 0}]
            client.import_profiles.return_value = 1
            client.speedtest_running.return_value = False
            client.logs.return_value = [
                {"Source": "SpeedtestService", "Message": "core failed to start"}]
            process = MagicMock()
            with patch.object(driver, "SUBS_DIR", base), \
                    patch.object(driver, "STATE_PATH", base / "state.json"), \
                    patch.object(driver, "RESULTS_PATH", base / "results.json"), \
                    patch.object(driver, "BLOCKED_PATH", base / "blocked.json"), \
                    patch.object(driver, "find_webapi_exe", return_value=base / "v2rayN.WebAPI.exe"), \
                    patch.object(driver, "start_webapi", return_value=process), \
                    patch.object(driver, "PattnClient", return_value=client), \
                    patch.object(driver, "stop_webapi"), \
                    patch("time.sleep"), \
                    patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(driver.main(["--webapi-dir", str(base)]), 0)
            self.assertIn("webapi log [SpeedtestService]: core failed to start", out.getvalue())
            self.assertIn("untested 1", out.getvalue())
            self.assertIn("testable 1/1", out.getvalue())
            self.assertIn("operations right after start", out.getvalue())
            self.assertIn("webapi log entries fetched: 1", out.getvalue())
            self.assertTrue((base / "tier1_all.txt").exists())

    def test_count_delays_separates_passed_failed_and_untested(self):
        driver = self._load_driver()
        profiles = [{"Delay": 120}, {"Delay": -1}, {"Delay": 0}, {}, {"Delay": "fast"}]
        self.assertEqual(driver.count_delays(profiles),
                         {"passed": 1, "failed": 1, "untested": 3})

    def test_main_tolerates_dead_and_failing_workers_in_log_fetch(self):
        import io
        from unittest.mock import MagicMock
        driver = self._load_driver()
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            lines = "".join(
                f"vless://11111111-1111-1111-1111-111111111111@10.0.2.{i}:443#t{i}\n"
                for i in range(1, 4))
            (base / "all.txt").write_text(lines, encoding="utf-8")
            # A stalls (one untested profile survives), B resolves then
            # fails on its fifth profiles() call — the tail's read —,
            # C dies while its chunk is assigned, D takes the requeued
            # chunk and resolves it.
            fakes = [_FakeWorkerClient(resolve=False),
                     _FakeWorkerClient(fail_on_call=5),
                     _FakeWorkerClient(fail_on_call=1),
                     _FakeWorkerClient()]
            process = MagicMock()
            with patch.object(driver, "SUBS_DIR", base), \
                    patch.object(driver, "STATE_PATH", base / "state.json"), \
                    patch.object(driver, "RESULTS_PATH", base / "results.json"), \
                    patch.object(driver, "BLOCKED_PATH", base / "blocked.json"), \
                    patch.object(driver, "find_webapi_exe",
                                 return_value=base / "v2rayN.WebAPI.exe"), \
                    patch.object(driver, "start_webapi", return_value=process), \
                    patch.object(driver, "PattnClient", side_effect=fakes), \
                    patch.object(driver, "stop_webapi"), \
                    patch("time.sleep"), \
                    patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(driver.main(["--webapi-dir", str(base),
                                              "--speedtest-page-size", "1"]), 0)
            self.assertIn("untested 1", out.getvalue())
            self.assertIn("webapi log entries fetched: 0", out.getvalue())

    def test_main_spreads_chunks_across_workers(self):
        import io
        from unittest.mock import MagicMock

        class MainFakeClient:
            """Resolves every imported profile to Delay 120, keeping the
            URI's own host/port so the driver's delay mapping matches."""

            def __init__(self):
                self._profiles = []

            def login(self):
                return "fake-token"

            def profiles(self):
                return [dict(p) for p in self._profiles]

            def import_profiles(self, uris):
                for uri in uris:
                    hostport = uri.split("@", 1)[1].split("#", 1)[0]
                    host, _, port = hostport.rpartition(":")
                    self._profiles.append(
                        {"IndexId": str(len(self._profiles) + 1), "Protocol": "vless",
                         "Address": host, "Port": int(port), "Delay": 0,
                         "CanTest": True, "CoreType": "xray"})
                return len(uris)

            def start_speedtest(self, profile_ids=None, action="Realping"):
                for p in self._profiles:
                    if p["Delay"] == 0:
                        p["Delay"] = 120

            def operations(self):
                return []

            def speedtest_running(self):
                return False

            def logs(self, limit=50, filter_text=None):
                return []

        driver = self._load_driver()
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            lines = "".join(
                f"vless://11111111-1111-1111-1111-111111111111@10.0.1.{i}:443#t{i}\n"
                for i in range(1, 251))
            (base / "all.txt").write_text(lines, encoding="utf-8")
            fakes = [MainFakeClient() for _ in range(4)]
            process = MagicMock()
            with patch.object(driver, "SUBS_DIR", base), \
                    patch.object(driver, "STATE_PATH", base / "state.json"), \
                    patch.object(driver, "RESULTS_PATH", base / "results.json"), \
                    patch.object(driver, "BLOCKED_PATH", base / "blocked.json"), \
                    patch.object(driver, "find_webapi_exe",
                                 return_value=base / "v2rayN.WebAPI.exe"), \
                    patch.object(driver, "start_webapi", return_value=process), \
                    patch.object(driver, "PattnClient", side_effect=fakes), \
                    patch.object(driver, "stop_webapi"), \
                    patch("time.sleep"), \
                    patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(driver.main(["--webapi-dir", str(base)]), 0)
            self.assertIn("250 due in 3 chunks of up to 100 across 4 workers",
                          out.getvalue())
            self.assertIn("passed 250", out.getvalue())
            self.assertEqual(sum(len(f._profiles) for f in fakes), 250)
            feed = (base / "tier1_all.txt").read_text(encoding="utf-8")
            self.assertEqual(sum(1 for line in feed.splitlines() if "://" in line), 250)

    def test_no_feeds_aborts(self):
        driver = self._load_driver()
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch.object(driver, "SUBS_DIR", base), \
                    patch.object(driver, "STATE_PATH", base / "state.json"), \
                    patch.object(driver, "BLOCKED_PATH", base / "blocked.json"), \
                    patch("sys.stderr", new_callable=io.StringIO) as err:
                self.assertEqual(driver.main(["--webapi-dir", str(base)]), 1)
            self.assertIn("no feeds to test", err.getvalue())

    def test_a_missing_feed_is_skipped_not_fatal(self):
        driver = self._load_driver()
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "all.txt").write_text(
                "vless://11111111-1111-1111-1111-111111111111@1.2.3.4:443#test\n",
                encoding="utf-8")
            with patch.object(driver, "SUBS_DIR", base), \
                    patch.object(driver, "STATE_PATH", base / "state.json"), \
                    patch.object(driver, "BLOCKED_PATH", base / "blocked.json"), \
                    patch.object(driver, "find_webapi_exe", return_value=base / "v2rayN.WebAPI.exe"), \
                    patch.object(driver, "start_webapi", side_effect=PattnError("no webapi here")), \
                    patch("sys.stderr", new_callable=io.StringIO) as err:
                # The run gets past the feed loop and fails at the WebAPI,
                # which is exactly what a missing active.txt must not block.
                with self.assertRaises(PattnError):
                    driver.main(["--webapi-dir", str(base)])
            self.assertIn("feed active.txt is missing or empty; skipping", err.getvalue())


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


class TierVariantCountsTests(unittest.TestCase):
    def test_tier1_variant_files_match_results_json(self):
        # The README stats table has no rows for tier-1 variants (they
        # are published by the tier-1 workflow, not the aggregator), so
        # test_rebrand.py skips its README-count check for them; pin
        # their row counts here against tier1_results.json instead.
        # Variants hold the passing configs plus the GitHub-blocked set
        # (see src/tier1.build_variants / build_results).
        root = Path(__file__).resolve().parents[1]
        results = json.loads((root / "data/tier1_results.json").read_text(encoding="utf-8"))
        self.assertEqual(set(results["feeds"]), set(TIER_GROUPS))
        for group, counts in sorted(results["feeds"].items()):
            path = root / "subs" / f"tier1_{group}.txt"
            rows = [line for line in path.read_text(encoding="utf-8").splitlines()
                    if line and not line.startswith("#")]
            self.assertEqual(len(rows), counts["passed"] + counts["github_blocked"],
                             f"tier1_{group}.txt rows != tier1_results.json passed+blocked")


if __name__ == "__main__":
    unittest.main()
