from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import test_iran_reachability as cli

SHA = "a" * 40
DIGEST = "b" * 64
URI = "trojan://public-fixture@1.1.1.1:443#Public\n"


class TestStandaloneCommand(unittest.TestCase):
    def test_missing_core_and_legacy_flags_are_rejected(self):
        parser = cli.build_arg_parser()
        for argv in ([], ["--sync-pattn-db"], ["--from-github"], ["--force"], ["--db-path", "sentinel"], ["--use-local-iran"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parser.parse_args(argv)

    def test_client_scripts_deleted(self):
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "scripts/sync_to_pattn.py").exists())
        self.assertFalse((root / "scripts/trigger_update.ps1").exists())

    def test_invalid_limits_and_missing_attestations_stop_before_authority_or_probe(self):
        for kwargs in ({"max_nodes": 0}, {"rounds": 0}, {"timeout": float("nan")}, {"concurrency": -1},
                       {"batch_size": 0}, {"attest_direct_iran": False}, {"attest_access_reviewed": False}):
            options = dict(xray_bin="supplied", source_sha=SHA, source_digest=DIGEST,
                           tester_revision=SHA, attest_direct_iran=True, attest_access_reviewed=True)
            options.update(kwargs)
            with self.subTest(kwargs=kwargs), patch.object(cli, "authorize_owner", side_effect=AssertionError("authority should not run")), \
                 patch.object(cli, "check_nodes_health", side_effect=AssertionError("probe should not run")), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.run_iran_check(**options), 1)

    def test_negative_auth_stops_before_binary_source_or_probe(self):
        from src.iran_results import IranResultsError
        with patch.object(cli, "authorize_owner", side_effect=IranResultsError("Owner authorization failed")), \
             patch.object(cli, "inspect_core", side_effect=AssertionError("binary should not run")), \
             patch.object(cli, "load_official_snapshot", side_effect=AssertionError("source should not run")), \
             patch.object(cli, "check_nodes_health", side_effect=AssertionError("probe should not run")), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.run_iran_check(xray_bin="supplied", source_sha=SHA, source_digest=DIGEST,
                tester_revision=SHA, attest_direct_iran=True, attest_access_reviewed=True), 1)

    def test_master_entrypoint_stops_before_any_project_import(self):
        import builtins
        import subprocess
        original_import = builtins.__import__
        script = Path(__file__).resolve().parents[1] / "scripts/test_iran_reachability.py"
        def imports(name, *args, **kw):
            if name == "src" or name.startswith("src."):
                raise AssertionError("master must not import project code")
            return original_import(name, *args, **kw)
        def git(argv, **kw):
            output = b"master\n" if "--abbrev-ref" in argv else SHA.encode() + b"\n"
            kw["stdout"].write(output)
            return subprocess.CompletedProcess(argv, 0)
        argv = [str(script), "--xray-bin", "supplied", "--source-sha", SHA,
                "--source-digest", DIGEST, "--tester-revision", SHA]
        with patch.object(sys, "argv", argv), patch.object(builtins, "__import__", side_effect=imports), \
             patch("subprocess.run", side_effect=git), contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as result:
                runpy.run_path(str(script), run_name="__main__")
        self.assertEqual(result.exception.code, 1)
        self.assertNotIn("supplied", out.getvalue())

    def test_official_snapshot_has_no_fallback_or_mixed_input(self):
        from src.iran_results import IranResultsError, load_official_snapshot
        content = URI.encode()
        digest = hashlib.sha256(content).hexdigest()
        with patch("src.iran_results._fetch_official_bytes", return_value=(200, content)) as fetch:
            nodes = load_official_snapshot(SHA, digest)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(fetch.call_args.args[0], f"https://raw.githubusercontent.com/mani-moh/navaroute/{SHA}/subs/all.txt")
        for status, body, want_digest in ((500, content, digest), (200, content, DIGEST),
                (200, content + b"file:///private/sentinel\n", hashlib.sha256(content + b"file:///private/sentinel\n").hexdigest())):
            with self.subTest(status=status, body=body), patch("src.iran_results._fetch_official_bytes", return_value=(status, body)):
                with self.assertRaises(IranResultsError):
                    load_official_snapshot(SHA, want_digest)

    def test_official_download_never_attaches_environment_proxy_service(self):
        from src.iran_results import load_official_snapshot
        from tests.test_network import WireSocket, response
        import socket
        from unittest.mock import MagicMock
        payload = URI.encode()
        fixture = WireSocket(response(payload))
        context = MagicMock()
        context.wrap_socket.return_value = fixture
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:7890", "https_proxy": "http://127.0.0.1:7890"}), \
             patch("src.network.socket.socket", return_value=fixture), \
             patch("src.network.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]), \
             patch("src.network.ssl.create_default_context", return_value=context):
            nodes = load_official_snapshot(SHA, hashlib.sha256(payload).hexdigest())
        self.assertEqual(len(nodes), 1)
        self.assertEqual(fixture.connected, ("1.1.1.1", 443))
        self.assertNotIn(b"CONNECT", fixture.sent)

    def test_direct_official_download_rejects_oversize_incomplete_and_ambiguous_framing(self):
        from src import iran_results as results
        from tests.test_network import WireSocket
        import socket
        from unittest.mock import MagicMock
        for wire in (b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nlarge",
                     b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nx",
                     b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"):
            fixture = WireSocket(wire)
            context = MagicMock()
            context.wrap_socket.return_value = fixture
            with self.subTest(wire=wire), patch.object(results, "MAX_SNAPSHOT_BYTES", 4), \
                 patch("src.network.socket.socket", return_value=fixture), \
                 patch("src.network.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]), \
                 patch("src.network.ssl.create_default_context", return_value=context), self.assertRaises(results.IranResultsError):
                results._fetch_official_bytes(f"https://raw.githubusercontent.com/mani-moh/navaroute/{SHA}/subs/all.txt")
            self.assertTrue(fixture.closed)

    def test_supplied_binary_missing_empty_or_relative_has_no_download_or_lookup(self):
        from src.iran_results import IranResultsError, inspect_core
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "supplied-core"
            empty.touch()
            for path in ("xray", str(empty), str(Path(tmp) / "missing")):
                with self.subTest(path=path), patch("src.iran_results._command", side_effect=AssertionError("invalid core must not execute")), \
                     self.assertRaises(IranResultsError):
                    inspect_core(path)

    def test_completed_local_run_does_not_publish_without_intent(self):
        from src.models import ProxyNode
        node = ProxyNode("trojan", "1.1.1.1", 443, "public-fixture")

        def probe(nodes, **kwargs):
            nodes[0].healthcheck_kind = "xray"
            nodes[0].healthchecked_at = "2026-11-10T12:00:00+00:00"
            nodes[0].passed_healthcheck = True
            nodes[0].latency_ms = 12.0
            return {"tested": 1, "passed": 1, "failed": 0, "unsupported": 0, "untested": 0}

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(cli, "authorize_owner", return_value={"head": SHA}), \
             patch.object(cli, "verify_tester_checkout"), \
             patch.object(cli, "inspect_core", return_value={"version": "Xray 25.10.15", "sha256": DIGEST}), \
             patch.object(cli, "load_official_snapshot", return_value=[node]), \
             patch.object(cli, "verify_source_commit"), \
             patch.object(cli, "check_nodes_health", side_effect=probe), \
             patch.object(cli, "publish_results", side_effect=AssertionError("unrequested publication")), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.run_iran_check(xray_bin="supplied", source_sha=SHA, source_digest=DIGEST,
                tester_revision=SHA, attest_direct_iran=True, attest_access_reviewed=True,
                recovery_dir=Path(tmp)), 0)
            self.assertNotIn("published", out.getvalue().lower())
            self.assertEqual(len(list(Path(tmp).glob("*.json"))), 1)

    def test_dated_retry_keeps_original_parent_and_does_not_retest(self):
        from tests.test_iran_results import manifest_for
        from src.models import ProxyNode
        from src.iran_results import IranResultsError
        nodes = [ProxyNode("trojan", "1.1.1.1", 443, "public-fixture")]
        envelope = {"captured_head": SHA, "manifest": manifest_for(nodes)}
        content = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        with tempfile.TemporaryDirectory() as tmp:
            recovery = Path(tmp) / (hashlib.sha256(content).hexdigest() + ".json")
            recovery.write_bytes(content)
            with patch.object(cli, "authorize_owner", return_value={"head": "c" * 40}), \
                 patch.object(cli, "verify_tester_checkout"), patch.object(cli, "verify_source_commit"), \
                 patch.object(cli, "inspect_core", return_value={"version": "Xray 25.10.15", "sha256": DIGEST}), \
                 patch.object(cli, "load_official_snapshot", return_value=nodes), \
                 patch.object(cli, "check_nodes_health", side_effect=AssertionError("retry must not retest")), \
                 patch.object(cli, "publish_results", side_effect=IranResultsError("conflict")) as publish, \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cli.run_iran_check(xray_bin="supplied", source_sha=SHA, source_digest=DIGEST,
                    tester_revision=SHA, attest_direct_iran=True, attest_access_reviewed=True, publish=True,
                    recovery_dir=Path(tmp), retry_manifest=recovery), 1)
                self.assertEqual(publish.call_args.kwargs["captured_head"], SHA)
                self.assertNotIn("published", out.getvalue().lower())

    def test_infrastructure_or_zero_work_does_not_write_evidence(self):
        from src.models import ProxyNode
        from src.healthcheck import CoreInfrastructureError
        for result in (CoreInfrastructureError("unavailable"), RuntimeError("private-sentinel"), {"tested": 0}):
            node = ProxyNode("trojan", "1.1.1.1", 443, "public-fixture")
            with self.subTest(result=result), tempfile.TemporaryDirectory() as tmp:
                old = Path(tmp) / "existing.json"
                old.write_text("old-evidence")
                with patch.object(cli, "authorize_owner", return_value={"head": SHA}), \
                     patch.object(cli, "verify_tester_checkout"), patch.object(cli, "verify_source_commit"), \
                     patch.object(cli, "inspect_core", return_value={"version": "Xray 25.10.15", "sha256": DIGEST}), \
                     patch.object(cli, "load_official_snapshot", return_value=[node]), \
                     patch.object(cli, "check_nodes_health", side_effect=result if isinstance(result, Exception) else None, return_value=result), \
                     patch.object(cli, "publish_results", side_effect=AssertionError("no genuine evidence")), \
                     contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(cli.run_iran_check(xray_bin="supplied", source_sha=SHA, source_digest=DIGEST,
                        tester_revision=SHA, attest_direct_iran=True, attest_access_reviewed=True,
                        recovery_dir=Path(tmp), publish=True), 1)
                self.assertNotIn("private-sentinel", out.getvalue())
                self.assertEqual(old.read_text(), "old-evidence")
                self.assertEqual(list(Path(tmp).iterdir()), [old])


class TestRetainedScheduledGates(unittest.TestCase):
    def test_existing_schedules_and_ci_are_retained_without_iran_workflow(self):
        root = Path(__file__).resolve().parents[1]
        update = (root / ".github/workflows/update-subs.yml").read_text(encoding="utf-8")
        daily = (root / ".github/workflows/daily-deep-discovery.yml").read_text(encoding="utf-8")
        ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "0 */6 * * *"', update)
        self.assertIn('cron: "0 2 * * *"', daily)
        self.assertIn("--discovery-api-requests", daily)
        self.assertIn("python -m unittest discover -s tests -v", ci)
        self.assertIn("Static security scan passed", ci)
        self.assertFalse(any("iran" in path.stem.lower() for path in (root / ".github/workflows").glob("*.yml")))


if __name__ == "__main__":
    unittest.main()
