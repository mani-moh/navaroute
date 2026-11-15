"""Tests for the tier-2 owner-PC app (tools/tier2/tier2_pattn.py).

The WebAPI itself is not run here (no Windows host in CI); only the
upload path is tested, with the GitHub API (_gh) and raw fetches (_raw)
mocked. The test-run path is exercised with a FakeClient standing in
for PattnClient (same start_speedtest/profiles surface), so the UDP
mode, the UDP->Realping fallback, scopes, the max-delay cutoff, Stop
and the ETA bookkeeping are covered without a server. The Windows
credential calls are guarded by os.name != "nt", so the module imports
cleanly on Linux.
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "tier2"))

import tier2_pattn  # noqa: E402
from src.models import SourceConfig  # noqa: E402
from src.parsers import parse_proxy_uri  # noqa: E402
from src.pattn_client import PattnError  # noqa: E402

SOURCE = SourceConfig("tier2", "Tier 2", "https://github.com/mani-moh/navaroute")

A = "vless://11111111-1111-1111-1111-111111111111@10.0.0.1:443#node-a"
B = "vless://22222222-2222-2222-2222-222222222222@10.0.0.2:443#node-b"
C = "vless://33333333-3333-3333-3333-333333333333@10.0.0.3:443#node-c"
D = "vless://44444444-4444-4444-4444-444444444444@10.0.0.4:443#node-d"


def _fp(uri: str) -> str:
    node = parse_proxy_uri(uri, SOURCE)
    assert node is not None
    return node.semantic_fingerprint()


def _feed_text(uris: list[str]) -> str:
    return "".join(uri + "\n" for uri in uris)


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.app = tier2_pattn.Tier2App()
        # A passes (120 ms), B fails, C passes (50 ms), D never tested.
        self.app.results = {_fp(A): 120, _fp(B): None, _fp(C): 50}
        self.raw = {
            "/subs/all.txt": _feed_text([A, B, C, D]),
            "/subs/online.txt": _feed_text([A, B]),
            "/subs/online-week.txt": _feed_text([A, B, C]),
            "/subs/fastest-100.txt": _feed_text([A]),
            "/subs/balanced-500.txt": _feed_text([A, B, C, D]),
            "/subs/protocols/vless.txt": _feed_text([A, B, C]),
            "/subs/countries/de.txt": _feed_text([A, D]),
            "/subs/categories/reality.txt": _feed_text([B]),
            "/data/tier1_state.json": "{}",
            "/data/github_blocked.json": "{}",
        }
        self.tree = [
            {"type": "blob", "path": "subs/protocols/vless.txt"},
            {"type": "blob", "path": "subs/protocols/vless_base64.txt"},
            {"type": "blob", "path": "subs/protocols/vless-tested.txt"},
            {"type": "blob", "path": "subs/protocols/vless-tested-iran.txt"},
            {"type": "blob", "path": "subs/countries/de.txt"},
            {"type": "blob", "path": "subs/categories/reality.txt"},
            {"type": "blob", "path": "subs/all.txt"},
            {"type": "blob", "path": "README.md"},
        ]
        self.puts: dict[str, str] = {}
        self.pr_body = ""

    def _fake_gh(self, method, path, token, body=None):
        if method == "GET" and path == "/git/trees/master?recursive=1":
            return {"tree": self.tree}
        if method == "GET" and path == "/git/ref/heads/master":
            return {"object": {"sha": "abc123"}}
        if method == "POST" and path == "/git/refs":
            return {}
        if method == "GET" and path.startswith("/contents/"):
            raise PattnError("not found")
        if method == "PUT" and path.startswith("/contents/"):
            self.puts[path[len("/contents/"):]] = base64.b64decode(
                body["content"]).decode("utf-8")
            return {}
        if method == "POST" and path == "/pulls":
            self.pr_body = body["body"]
            return {"number": 7,
                    "html_url": "https://github.com/mani-moh/navaroute/pull/7"}
        raise AssertionError(f"unexpected _gh call: {method} {path}")

    def _fake_raw(self, path):
        if path in self.raw:
            return self.raw[path]
        raise PattnError(f"no such file: {path}")

    def _upload(self, groups=None):
        with patch.object(tier2_pattn, "_gh", side_effect=self._fake_gh), \
                patch.object(tier2_pattn, "_raw", side_effect=self._fake_raw):
            return self.app.upload("fake-token", groups)

    def test_upload_writes_slice_tested_iran_variants(self):
        url = self._upload()
        self.assertEqual(url, "https://github.com/mani-moh/navaroute/pull/7")
        # Core global variant: the passing configs, fastest delay first.
        self.assertEqual(self._rows("subs/tested-iran.txt"), [C, A])
        # Slice variants are the slice intersected with the Iran passing
        # set, sorted by Iran delay.
        self.assertEqual(self._rows("subs/protocols/vless-tested-iran.txt"), [C, A])
        self.assertEqual(self._rows("subs/countries/de-tested-iran.txt"), [A])
        # A slice with no passers still gets an empty file, not an error.
        self.assertEqual(self._rows("subs/categories/reality-tested-iran.txt"), [])
        # Base64 twins ride along for every variant.
        for name in ("tested-iran", "protocols/vless-tested-iran",
                     "countries/de-tested-iran", "categories/reality-tested-iran"):
            self.assertIn(f"subs/{name}_base64.txt", self.puts)
            decoded = base64.b64decode(
                self.puts[f"subs/{name}_base64.txt"]).decode("utf-8")
            self.assertEqual(decoded.splitlines(),
                             self._rows(f"subs/{name}.txt"))
        # The tree's base64 twins and existing tested variants are
        # outputs, never inputs: no variant is built from them.
        self.assertNotIn("subs/protocols/vless_base64-tested-iran.txt", self.puts)
        self.assertNotIn("subs/protocols/vless-tested-tested-iran.txt", self.puts)
        # iran_results.json stays core-only; slice variants are files only.
        results = json.loads(self.puts["data/iran_results.json"])
        self.assertEqual(set(results["feeds"]),
                         {"all", "online", "online-week", "fastest-100", "balanced-500"})
        self.assertEqual(results["feeds"]["all"], {"tested": 3, "passed": 2})
        self.assertEqual(results["feeds"]["online"], {"tested": 2, "passed": 1})
        self.assertEqual(results["totals"], {"tested": 12, "passed": 8})
        self.assertIn("3 slice -tested-iran variants", self.pr_body)

    def _rows(self, path: str) -> list[str]:
        text = self.puts[path]
        return [line for line in text.splitlines() if line and not line.startswith("#")]


class HelpersTests(unittest.TestCase):
    def test_select_todo_scopes(self):
        fps = ["a", "b", "c", "d"]
        results = {"a": 120, "b": None}
        self.assertEqual(tier2_pattn.select_todo(fps, results, "remaining"),
                         ["b", "c", "d"])
        self.assertEqual(tier2_pattn.select_todo(fps, results, "failed"), ["b"])
        self.assertEqual(tier2_pattn.select_todo(fps, results, "untested"),
                         ["c", "d"])
        self.assertEqual(tier2_pattn.select_todo(fps, results, "all"), fps)
        with self.assertRaises(PattnError):
            tier2_pattn.select_todo(fps, results, "nope")

    def test_passes_delay(self):
        self.assertTrue(tier2_pattn.passes_delay(120, 0))
        self.assertTrue(tier2_pattn.passes_delay(100, 100))
        self.assertFalse(tier2_pattn.passes_delay(120, 100))
        self.assertFalse(tier2_pattn.passes_delay(0, 0))
        self.assertFalse(tier2_pattn.passes_delay(-1, 0))
        self.assertFalse(tier2_pattn.passes_delay(None, 0))

    def test_eta_math(self):
        self.assertEqual(tier2_pattn.configs_per_min(60, 60.0), 60.0)
        self.assertEqual(tier2_pattn.configs_per_min(0, 10.0), 0.0)
        self.assertEqual(tier2_pattn.configs_per_min(10, 0.0), 0.0)
        self.assertEqual(tier2_pattn.estimate_seconds(120, 60.0), 120.0)
        self.assertIsNone(tier2_pattn.estimate_seconds(10, 0.0))
        self.assertIsNone(tier2_pattn.estimate_seconds(0, 60.0))
        self.assertEqual(tier2_pattn.format_duration(None), "—")
        self.assertEqual(tier2_pattn.format_duration(45), "45s")
        self.assertEqual(tier2_pattn.format_duration(90), "1m 30s")
        self.assertEqual(tier2_pattn.format_duration(3900), "1h 5m")

    def test_validate_options(self):
        good = tier2_pattn.validate_options(
            {"source": "all", "scope": "failed", "test_mode": "udp-realping",
             "page_size": "250", "max_delay": "100"})
        self.assertEqual(good["page_size"], 250)
        self.assertEqual(good["max_delay"], 100)
        self.assertEqual(tier2_pattn.validate_options({})["source"], "tier1")
        for bad in ({"source": "x"}, {"scope": "x"}, {"test_mode": "x"},
                    {"page_size": 0}, {"page_size": "many"},
                    {"max_delay": -1}):
            with self.assertRaises(PattnError, msg=str(bad)):
                tier2_pattn.validate_options(bad)


class FakeClient:
    """Stand-in for PattnClient: delays land the moment a test starts."""

    instances: list["FakeClient"] = []

    def __init__(self, api_key: str = "", **kwargs: object) -> None:
        self.api_key = api_key
        self.actions: list[str] = []
        self.imported: list[list[str]] = []
        self.stopped = False
        self.refuse_udp = False
        self.delay_map: dict[tuple[str, str], int] = {}  # (action, addr) -> ms
        self._profiles: list[dict] = []
        FakeClient.instances.append(self)

    def set_delay(self, action: str, address: str, delay: int) -> None:
        self.delay_map[(action, address)] = delay

    def login(self) -> str:
        return "token"

    def apply_iran_preset(self) -> None:
        pass

    def profiles(self) -> list[dict]:
        return [dict(p) for p in self._profiles]

    def delete_profiles(self, profile_ids: list[str]) -> None:
        self._profiles = []

    def import_profiles(self, uris: list[str]) -> int:
        self.imported.append(list(uris))
        for uri in uris:
            node = parse_proxy_uri(uri, SOURCE)
            assert node is not None
            self._profiles.append({"IndexId": str(len(self._profiles)),
                                  "Protocol": node.scheme, "Address": node.address,
                                  "Port": node.port, "Delay": 0})
        return len(uris)

    def start_speedtest(self, profile_ids: list[str] | None = None,
                        action: str = "Realping") -> None:
        self.actions.append(action)
        if action == "Udptest" and self.refuse_udp:
            raise PattnError("WebAPI POST /api/speedtests -> HTTP 400: ")
        for profile in self._profiles:
            delay = self.delay_map.get((action, profile["Address"]))
            if delay is None:
                delay = self.delay_map.get(("any", profile["Address"]), -1)
            profile["Delay"] = delay

    def stop_speedtest(self) -> None:
        self.stopped = True

    def speedtest_running(self) -> bool:
        return False


class RunTests(unittest.TestCase):
    """_run() end to end with the WebAPI faked out."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_patch = patch.object(
            tier2_pattn, "CACHE_DIR", Path(self.tmp.name))
        self.cache_patch.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.cache_patch.stop)
        FakeClient.instances.clear()
        self.app = tier2_pattn.Tier2App()
        self.feed = {"/subs/tested.txt": _feed_text([A, B, C]),
                     "/subs/all.txt": _feed_text([A, B, C, D])}
        self.page_sizes: list[int] = []

    def _patch_run(self):
        patches = [
            patch.object(tier2_pattn, "_raw", side_effect=lambda p: self.feed[p]),
            patch.object(tier2_pattn.Tier2App, "_webapi_exe",
                         return_value=Path("/tmp/fake/v2rayN.WebAPI.exe")),
            patch.object(tier2_pattn, "write_webapi_speedtest_config",
                         side_effect=lambda exe, size: self.page_sizes.append(size)),
            patch.object(tier2_pattn, "generate_api_key", return_value="testkey"),
            patch.object(tier2_pattn, "start_webapi", return_value=object()),
            patch.object(tier2_pattn, "stop_webapi", return_value=None),
            patch.object(tier2_pattn, "PattnClient", FakeClient),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _run(self, options: dict) -> FakeClient:
        """Run _run() synchronously with the WebAPI faked out."""
        self._patch_run()
        self.app._run(tier2_pattn.validate_options(options))
        return FakeClient.instances[-1]

    def _preset_delays(self, mapping: dict[str, dict[str, int]]) -> None:
        """Delays per action, applied to every FakeClient created later."""
        original_init = FakeClient.__init__

        def patched_init(self_client, api_key: str = "", **kwargs: object) -> None:
            original_init(self_client, api_key, **kwargs)
            for action, delays in mapping.items():
                for address, delay in delays.items():
                    self_client.set_delay(action, address, delay)

        patcher = patch.object(FakeClient, "__init__", patched_init)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_udp_mode_uses_udptest(self):
        self._preset_delays({"Udptest": {"10.0.0.1": 120, "10.0.0.2": -1,
                                         "10.0.0.3": 50}})
        client = self._run({"test_mode": "udp"})
        self.assertEqual(client.actions, ["Udptest"])
        self.assertEqual(self.app.results,
                         {_fp(A): 120, _fp(B): None, _fp(C): 50})
        self.assertEqual(self.app.run_mode, "udp")
        self.assertIs(self.app.udp_available, True)
        self.assertEqual(self.app.state["status"], "done")
        saved, mode = self.app._load_progress("tier1")
        self.assertEqual(mode, "udp")
        self.assertEqual(saved, self.app.results)

    def test_udp_refused_falls_back_to_realping(self):
        self._preset_delays({"Realping": {"10.0.0.1": 90, "10.0.0.2": -1,
                                          "10.0.0.3": 40}})
        self._patch_run()
        # The refusal flag lives on the instance; set it right after
        # the client is created by wrapping start_speedtest's owner.
        original_start = FakeClient.start_speedtest

        def refusing_start(self_client, profile_ids=None, action="Realping"):
            self_client.refuse_udp = True
            return original_start(self_client, profile_ids, action)

        with patch.object(FakeClient, "start_speedtest", refusing_start):
            self.app._run(tier2_pattn.validate_options({"test_mode": "udp"}))
        client = FakeClient.instances[-1]
        self.assertEqual(client.actions, ["Udptest", "Realping"])
        self.assertEqual(self.app.run_mode, "realping")
        self.assertIs(self.app.udp_available, False)
        self.assertEqual(self.app.results,
                         {_fp(A): 90, _fp(B): None, _fp(C): 40})
        self.assertIn("refused Udptest", " ".join(self.app.state["log"]))

    def test_udp_realping_confirms_only_passers(self):
        self._preset_delays({
            "Udptest": {"10.0.0.1": 120, "10.0.0.2": -1, "10.0.0.3": 50},
            "Realping": {"10.0.0.1": 90, "10.0.0.3": -1}})
        client = self._run({"test_mode": "udp-realping"})
        self.assertEqual(client.actions, ["Udptest", "Realping"])
        self.assertEqual(client.imported, [[A, B, C], [A, C]])
        self.assertEqual(self.app.results,
                         {_fp(A): 90, _fp(B): None, _fp(C): None})
        self.assertEqual(self.app.run_mode, "udp-realping")

    def test_max_delay_cutoff_flags_slow_passers(self):
        self._preset_delays({"Realping": {"10.0.0.1": 120, "10.0.0.2": -1,
                                          "10.0.0.3": 50}})
        self._run({"max_delay": 100})
        self.assertEqual(self.app.results,
                         {_fp(A): None, _fp(B): None, _fp(C): 50})

    def test_scope_failed_retests_only_failures(self):
        self.app.results = {_fp(A): 120, _fp(B): None}
        self.app._save_progress("all")
        client = self._run({"source": "all", "scope": "failed"})
        self.assertEqual(client.imported, [[B]])
        self.assertIsNone(self.app.results[_fp(B)])

    def test_scope_untested_skips_tested(self):
        self.app.results = {_fp(A): 120, _fp(B): None}
        self.app._save_progress("all")
        client = self._run({"source": "all", "scope": "untested"})
        self.assertEqual(client.imported, [[C, D]])

    def test_scope_all_retests_everything(self):
        self.app.results = {_fp(A): 120, _fp(B): None}
        self.app._save_progress("all")
        client = self._run({"source": "all", "scope": "all"})
        self.assertEqual(client.imported, [[A, B, C, D]])

    def test_page_size_is_applied(self):
        self._preset_delays({"Realping": {}})
        self._run({"page_size": 250})
        self.assertEqual(self.page_sizes, [250])

    def test_estimate_comes_from_last_rate(self):
        (Path(self.tmp.name) / "last-rate.json").write_text(
            json.dumps({"configs_per_min": 60.0, "tested": 60,
                        "elapsed_s": 60.0, "at": "2026-11-16T00:00:00+00:00"}),
            encoding="utf-8")
        self._preset_delays({"Realping": {}})
        self._run({})
        self.assertEqual(self.app.state["estimated_s"], 3.0)
        self.assertEqual(self.app.state["last_rate_per_min"], 60.0)
        saved = json.loads((Path(self.tmp.name) / "last-rate.json").read_text(
            encoding="utf-8"))
        self.assertGreater(saved["configs_per_min"], 0)

    def test_stop_stops_cleanly(self):
        self._patch_run()
        self.app._stop_requested.set()
        self.app._run(tier2_pattn.validate_options({}))
        self.assertEqual(self.app.state["status"], "stopped")
        self.assertIn("Stopped by the user", " ".join(self.app.state["log"]))
        self.assertEqual(FakeClient.instances[-1].imported, [])

    def test_stop_method_signals_client(self):
        client = FakeClient()
        self.app._client = client
        self.app.stop()
        self.assertTrue(self.app._stop_requested.is_set())
        self.assertTrue(client.stopped)

    def test_wait_returns_early_on_stop(self):
        client = FakeClient()
        client.import_profiles([A, B])
        event = threading.Event()
        event.set()
        profiles = self.app._wait(client, 2, stop_event=event)
        self.assertTrue(client.stopped)
        self.assertEqual(len(profiles), 2)


class GroupUploadTests(unittest.TestCase):
    def setUp(self):
        self.app = tier2_pattn.Tier2App()
        self.app.results = {_fp(A): 120, _fp(B): None, _fp(C): 50}
        self.app.run_mode = "realping"
        self.raw = {
            "/subs/all.txt": _feed_text([A, B, C, D]),
            "/subs/online.txt": _feed_text([A, B]),
            "/subs/online-week.txt": _feed_text([A, B, C]),
            "/subs/fastest-100.txt": _feed_text([A]),
            "/subs/balanced-500.txt": _feed_text([A, B, C, D]),
            "/subs/protocols/vless.txt": _feed_text([A, B, C]),
            "/subs/countries/de.txt": _feed_text([A, D]),
            "/subs/categories/reality.txt": _feed_text([B]),
            "/data/tier1_state.json": "{}",
            "/data/github_blocked.json": "{}",
        }
        self.tree = [
            {"type": "blob", "path": "subs/protocols/vless.txt"},
            {"type": "blob", "path": "subs/countries/de.txt"},
            {"type": "blob", "path": "subs/categories/reality.txt"},
        ]
        self.puts: dict[str, str] = {}
        self.pr_body = ""

    def _fake_gh(self, method, path, token, body=None):
        if method == "GET" and path == "/git/trees/master?recursive=1":
            return {"tree": self.tree}
        if method == "GET" and path == "/git/ref/heads/master":
            return {"object": {"sha": "abc123"}}
        if method == "POST" and path == "/git/refs":
            return {}
        if method == "GET" and path.startswith("/contents/"):
            raise PattnError("not found")
        if method == "PUT" and path.startswith("/contents/"):
            self.puts[path[len("/contents/"):]] = base64.b64decode(
                body["content"]).decode("utf-8")
            return {}
        if method == "POST" and path == "/pulls":
            self.pr_body = body["body"]
            return {"number": 7,
                    "html_url": "https://github.com/mani-moh/navaroute/pull/7"}
        raise AssertionError(f"unexpected _gh call: {method} {path}")

    def _fake_raw(self, path):
        if path in self.raw:
            return self.raw[path]
        raise PattnError(f"no such file: {path}")

    def _upload(self, groups=None):
        with patch.object(tier2_pattn, "_gh", side_effect=self._fake_gh), \
                patch.object(tier2_pattn, "_raw", side_effect=self._fake_raw):
            return self.app.upload("fake-token", groups)

    def test_group_selection_uploads_only_chosen_groups(self):
        self._upload(["all"])
        self.assertIn("subs/tested-iran.txt", self.puts)
        self.assertNotIn("subs/online-tested-iran.txt", self.puts)
        self.assertNotIn("subs/protocols/vless-tested-iran.txt", self.puts)
        results = json.loads(self.puts["data/iran_results.json"])
        self.assertEqual(set(results["feeds"]), {"all"})
        self.assertEqual(results["totals"], {"tested": 3, "passed": 2})
        self.assertEqual(results["mode"], "realping")
        self.assertIn("0 slice -tested-iran variants", self.pr_body)

    def test_group_selection_slices_shorthand(self):
        self._upload(["all", "slices"])
        self.assertIn("subs/tested-iran.txt", self.puts)
        self.assertIn("subs/protocols/vless-tested-iran.txt", self.puts)
        self.assertNotIn("subs/online-tested-iran.txt", self.puts)
        self.assertIn("3 slice -tested-iran variants", self.pr_body)

    def test_udp_mode_is_labelled_in_results_and_pr(self):
        self.app.run_mode = "udp"
        self._upload()
        results = json.loads(self.puts["data/iran_results.json"])
        self.assertEqual(results["mode"], "udp")
        self.assertIn("Iran UDP results", self.pr_body)
        self.assertIn("NOTE: UDP mode only", self.pr_body)


if __name__ == "__main__":
    unittest.main()
